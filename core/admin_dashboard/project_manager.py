"""
project_manager.py
-------------------
Every place the old main.py touched nexus_project.json directly
(create_new_project, save_blueprint_to_project, save_template_session,
start_live_grading_session, export_group_results) now goes through this
class instead. Benefits:
  - One place knows the on-disk layout (nexus_project.json, roster.db,
    cropped_template.jpg) — change it once, not in eight methods.
  - Pages/dashboard code reads like business logic, not file plumbing.
  - Easy to unit test without spinning up any Qt widgets.

The actual `students` table SQL lives in RosterRepository and the
`grades`/`sessions` SQL lives in GradingRepository — this class owns
workspace lifecycle + JSON config I/O and delegates to those for
anything SQLite, so a change to roster/grade schema doesn't require
touching this file.
"""

import os
import json
from admin_dashboard.recent_projects import recent_projects
from admin_dashboard.exam_modes import DEFAULT_MODE_ID, SINGLE_VERSION_KEY, get_mode_by_id
from admin_dashboard.group_registry import group_registry
from admin_dashboard.roster_repository import RosterRepository


CONFIG_FILENAME = "nexus_project.json"
DB_FILENAME = "roster.db"
MCQ_LAYOUT_COLS = 3  # bubble sheet studio always spreads MCQs across 3 columns


class ProjectManager:
    """Owns the active workspace's directory + on-disk state."""

    def __init__(self):
        self.project_dir: str | None = None
        self.project_name: str | None = None

    # ------------------------------------------------------------------
    # Workspace lifecycle
    # ------------------------------------------------------------------
    @property
    def is_active(self) -> bool:
        return self.project_dir is not None

    @property
    def config_path(self) -> str:
        return os.path.join(self.project_dir, CONFIG_FILENAME)

    @property
    def db_path(self) -> str:
        return os.path.join(self.project_dir, DB_FILENAME)

    def create_project(self, display_name: str, parent_dir: str):
        """Creates a new workspace folder with a default config file."""
        self.project_name = display_name
        self.project_dir = os.path.join(parent_dir, display_name.replace(" ", "_"))
        os.makedirs(self.project_dir, exist_ok=True)
        default_config = {"project_name": display_name, "mode": DEFAULT_MODE_ID, "mcq_count": 10}
        with open(self.config_path, "w") as f:
            json.dump(default_config, f)

        recent_projects.touch(display_name, self.project_dir)

    def open_project(self, dir_path: str) -> bool:
        """Loads an existing workspace. Returns False if invalid (no config found)."""
        candidate_config = os.path.join(dir_path, CONFIG_FILENAME)
        if not os.path.exists(candidate_config):
            return False

        with open(candidate_config, "r") as f:
            data = json.load(f)

        self.project_name = data.get("project_name", "Unknown Exam")
        self.project_dir = dir_path
        recent_projects.touch(self.project_name, dir_path)
        RosterRepository.purge_orphaned_groups(self.db_path, set(group_registry.list_groups()))
        return True

    # ------------------------------------------------------------------
    # Config (blueprint / template) read-modify-write
    # ------------------------------------------------------------------
    def load_config(self) -> dict:
        with open(self.config_path, "r") as f:
            return json.load(f)

    def _update_config(self, **fields):
        config = self.load_config()
        config.update(fields)
        with open(self.config_path, "w") as f:
            json.dump(config, f, indent=4)

    def save_blueprint(self, mode_id: str, mcq_count: int, mcq_ranges: list[dict],
                       has_essays: bool, essay_points_map: dict,
                       choices_per_question: int, id_letters: str):
        self._update_config(
            project_name=self.project_name,
            mode=mode_id,
            mcq_count=mcq_count,
            mcq_ranges=mcq_ranges,
            has_essays=has_essays,
            essay_points_map=essay_points_map,
            choices_per_question=choices_per_question,
            id_letters=id_letters,
        )

    @staticmethod
    def compute_mcq_column_layout(mcq_count: int, num_cols: int = MCQ_LAYOUT_COLS) -> dict:
        """Mirrors the Bubble Sheet Studio's column-split math exactly
        (col1 = ceil(n/3), col2 = ceil(remaining/2), col3 = whatever is
        left), generalized to any column count: each column takes the
        ceiling of the questions still remaining divided by the columns
        still left, so the studio's 3-column formula falls out as the
        default case. Returns e.g. {"num_cols": 3, "columns": {"1": 9,
        "2": 8, "3": 8}} for 25 questions."""
        remaining = max(0, mcq_count)
        cols_left = max(1, num_cols)
        columns = {}
        for i in range(1, num_cols + 1):
            n = -(-remaining // cols_left)  # ceil division
            columns[str(i)] = n
            remaining -= n
            cols_left -= 1
        return {"num_cols": num_cols, "columns": columns}

    def save_model_answers(self, answers_by_version: dict[str, dict], voided_by_version: dict[str, list]):
        """answers_by_version / voided_by_version are keyed by version label
        ("A", "B", ...). Quiz mode always has exactly one key
        (exam_modes.SINGLE_VERSION_KEY); Shamel mode may have several —
        same mcq_count/ranges/essays, different model answer per booklet.
        Storing both modes in this nested shape means nothing downstream
        (readiness checks, the sync packet) needs an if/else on mode."""
        self._update_config(
            model_answers=answers_by_version,
            voided_questions=voided_by_version,
            answer_versions=list(answers_by_version.keys()),
        )

    # ------------------------------------------------------------------
    # Roster / groups (delegates to RosterRepository) + Excel export
    # ------------------------------------------------------------------
    def get_groups(self) -> list[tuple[str, int]]:
        """Returns [(group_name, student_count), ...] for the group hub cards."""
        return RosterRepository.get_groups(self.db_path)

    def export_group_to_excel(self, group_name: str, save_path: str):
        import pandas as pd
        from admin_dashboard.grading_repository import GradingRepository

        GradingRepository.ensure_grades_schema(self.db_path)
        grades = GradingRepository.get_group_grades(self.db_path, group_name)
        df = pd.DataFrame(grades, columns=[
            "student_id", "group_type", "answer_version", "mcq_score", "essay_total", "final_score", "timestamp"
        ])
        df = df[["student_id", "group_type", "final_score"]]
        df.columns = ["Student ID", "Group Type", "Total Score"]
        df.to_excel(save_path, index=False)

    def clear_group_grades(self, group_name: str) -> int:
        """Deletes every saved grade for this group (all sessions), so the
        next export only reflects whatever a fresh live grading session
        produces. Returns the number of grade rows removed."""
        from admin_dashboard.grading_repository import GradingRepository

        GradingRepository.ensure_grades_schema(self.db_path)
        return GradingRepository.delete_group_grades(self.db_path, group_name)

    # ------------------------------------------------------------------
    # Sync packet sent to mobile clients over the socket
    # ------------------------------------------------------------------
    def build_sync_packet(self, group_name: str) -> dict:
        exam_config = self.load_config()
        mode_id = exam_config.get("mode", DEFAULT_MODE_ID)
        exam_mode = get_mode_by_id(mode_id)
        mcq_count = exam_config.get("mcq_count", 0)

        return {
            "exam_name": exam_config.get("project_name", self.project_name),
            "exam_mode": mode_id,
            "mcq_count": mcq_count,
            "mcq_ranges": exam_config.get("mcq_ranges", []),
            "has_essays": exam_config.get("has_essays", False),
            "essay_points_map": exam_config.get("essay_points_map", {}),
            "group_name": group_name,
            "answer_versions": exam_config.get("answer_versions", [SINGLE_VERSION_KEY]),
            "model_answers": exam_config.get("model_answers", {}),      # {version: {"1": "A", ...}}
            "voided_questions": exam_config.get("voided_questions", {}),  # {version: [q, ...]}
            "choices_per_question": exam_config.get("choices_per_question", 4),
            "mcq_columns": self.compute_mcq_column_layout(mcq_count),  # {"num_cols": 3, "columns": {"1": n, ...}}
            "id": {
                "num_digits": exam_mode.id_digit_count,  # derived from mode, not saved
                "num_letters": len(exam_config.get("id_letters", "CDEFMW")),
                "letters": list(exam_config.get("id_letters", "CDEFMW")),
            },
        }

    def get_session_password(self) -> str:
        return self.load_config().get("session_password", "12345678")

    def set_session_password(self, password: str):
        self._update_config(session_password=password)

    def get_blueprint_readiness(self) -> list[str]:
        """Returns human-readable problems blocking a meaningful sync
        packet. Empty list = ready to send."""
        if not self.is_active:
            return ["No active workspace."]

        config = self.load_config()
        problems = []

        mcq_count = config.get("mcq_count", 0)
        if mcq_count <= 0:
            problems.append("Exam Blueprint has not been configured.")

        ranges = config.get("mcq_ranges", [])
        covered = sum((r["end"] - r["start"] + 1) for r in ranges)
        if covered < mcq_count:
            problems.append("Mark ranges do not cover every MCQ question yet.")

        if "choices_per_question" not in config or "id_letters" not in config:
            problems.append("Layout parameters (choices per question, ID letters) have not been saved yet.")

        answers_by_version = config.get("model_answers", {})
        voided_by_version = config.get("voided_questions", {})
        versions = config.get("answer_versions", [SINGLE_VERSION_KEY])
        if not answers_by_version:
            problems.append("No model answer key has been saved yet.")
        for version in versions:
            answers = answers_by_version.get(version, {})
            voided = set(voided_by_version.get(version, []))
            missing = [
                q for q in range(1, mcq_count + 1)
                if q not in voided and str(q) not in answers
            ]
            if missing:
                label = f"Version {version}" if len(versions) > 1 else "Model answer key"
                problems.append(f"{label}: {len(missing)} question(s) missing an answer.")

        if config.get("has_essays") and not config.get("essay_points_map"):
            problems.append("Essay questions are enabled but have no point values saved.")

        return problems