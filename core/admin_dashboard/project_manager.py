"""
project_manager.py
-------------------
Every place the old main.py touched nexus_project.json or roster.db
directly (create_new_project, open_existing_project, save_blueprint_to_project,
save_template_session, refresh_group_hub, load_group_excel, open_group_detail,
start_live_grading_session, export_group_results) now goes through this class
instead. Benefits:
  - One place knows the on-disk layout (nexus_project.json, roster.db,
    cropped_template.jpg) — change it once, not in eight methods.
  - Pages/dashboard code reads like business logic, not file plumbing.
  - Easy to unit test without spinning up any Qt widgets.
"""

import os
import json
import sqlite3
from admin_dashboard.recent_projects import recent_projects
from admin_dashboard.exam_modes import DEFAULT_MODE_ID, SINGLE_VERSION_KEY



CONFIG_FILENAME = "nexus_project.json"
DB_FILENAME = "roster.db"
TEMPLATE_FILENAME = "cropped_template.jpg"
SOURCE_TEMPLATE_FILENAME = "source_template.jpg"


STUDENTS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS students (
        student_id TEXT PRIMARY KEY, student_name TEXT NOT NULL,
        is_present INTEGER DEFAULT 1, group_name TEXT
    )
"""


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
                       has_essays: bool, essay_points_map: dict):
        self._update_config(
            project_name=self.project_name,
            mode=mode_id,
            mcq_count=mcq_count,
            mcq_ranges=mcq_ranges,
            has_essays=has_essays,
            essay_points_map=essay_points_map,
        )

    def save_template(self, template_path: str, roi_coordinates: dict, source_template_path: str = None):
        fields = {"template_path": template_path, "roi_coordinates": roi_coordinates}
        if source_template_path:
            fields["source_template_path"] = source_template_path
        self._update_config(**fields)

    def template_save_path(self) -> str:
        return os.path.join(self.project_dir, TEMPLATE_FILENAME)

    def source_template_save_path(self) -> str:
        return os.path.join(self.project_dir, SOURCE_TEMPLATE_FILENAME)

    def clear_template(self):
        """Deletes the saved template images from disk and clears the
        related config fields — used by the ROI page's 'Clear Image' button."""
        for path in (self.template_save_path(), self.source_template_save_path()):
            if os.path.exists(path):
                os.remove(path)
        config = self.load_config()
        for key in ("template_path", "source_template_path", "roi_coordinates"):
            config.pop(key, None)
        with open(self.config_path, "w") as f:
            json.dump(config, f, indent=4)

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
    # Roster / groups (SQLite)
    # ------------------------------------------------------------------
    def _connect(self, row_factory=None):
        conn = sqlite3.connect(self.db_path)
        if row_factory:
            conn.row_factory = row_factory
        cursor = conn.cursor()
        cursor.execute(STUDENTS_TABLE_SQL)
        return conn, cursor

    def get_groups(self) -> list[tuple[str, int]]:
        """Returns [(group_name, student_count), ...] for the group hub cards."""
        if not os.path.exists(self.db_path):
            return []
        conn, cursor = self._connect()
        cursor.execute("SELECT group_name, COUNT(*) FROM students GROUP BY group_name")
        rows = cursor.fetchall()
        conn.close()
        return [(name, count) for name, count in rows if name]

    def get_group_student_count(self, group_name: str) -> int:
        if not os.path.exists(self.db_path):
            return 0
        conn, cursor = self._connect()
        cursor.execute("SELECT COUNT(*) FROM students WHERE group_name = ?", (group_name,))
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def import_excel_roster(self, file_path: str, group_name: str) -> tuple[int, int]:
        """Parses an Excel roster and inserts/updates students for group_name.
        Returns (rows_inserted_this_import, total_students_now_in_group)."""
        import pandas as pd

        df = pd.read_excel(file_path)
        df.columns = [str(col).lower().strip() for col in df.columns]

        if "id" not in df.columns or "name" not in df.columns:
            raise ValueError("Excel file must contain 'id' and 'name' columns.")

        df_clean = df.dropna(subset=["id"]).copy()
        if "s6" in df_clean.columns:
            df_clean["is_present"] = df_clean["s6"].apply(
                lambda x: 1 if pd.notnull(x) and str(x).strip() == "1" else 0
            )
        else:
            df_clean["is_present"] = 1

        conn, cursor = self._connect()
        total_inserted = 0
        for _, row in df_clean.iterrows():
            student_id = str(row["id"]).strip()
            student_name = str(row["name"]).strip()
            is_present = int(row["is_present"])
            cursor.execute(
                "INSERT OR REPLACE INTO students (student_id, student_name, is_present, group_name) "
                "VALUES (?, ?, ?, ?)",
                (student_id, student_name, is_present, group_name),
            )
            total_inserted += 1
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM students WHERE group_name = ?", (group_name,))
        total_in_group = cursor.fetchone()[0]
        conn.close()
        return total_inserted, total_in_group

    def get_roster(self, group_name: str) -> list[dict]:
        """Returns the roster list shape needed for the sync packet sent to phones."""
        conn, cursor = self._connect(row_factory=sqlite3.Row)
        cursor.execute(
            "SELECT student_id, student_name, is_present FROM students WHERE group_name = ?",
            (group_name,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [{"id": r["student_id"], "name": r["student_name"], "present": r["is_present"]} for r in rows]

    def export_group_to_excel(self, group_name: str, save_path: str):
        import pandas as pd

        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query(
            "SELECT student_id AS ID, student_name AS Name, is_present AS Status "
            "FROM students WHERE group_name = ?",
            conn, params=(group_name,),
        )
        conn.close()
        df["Status"] = df["Status"].apply(lambda x: "Present" if x == 1 else "Absent")
        df.to_excel(save_path, index=False)

    # ------------------------------------------------------------------
    # Sync packet sent to mobile clients over the socket
    # ------------------------------------------------------------------
    def build_sync_packet(self, group_name: str) -> dict:
        exam_config = self.load_config()
        return {
            "exam_name": exam_config.get("project_name", self.project_name),
            "exam_mode": exam_config.get("mode", "quiz"),
            "mcq_count": exam_config.get("mcq_count", 0),
            "mcq_ranges": exam_config.get("mcq_ranges", []),
            "has_essays": exam_config.get("has_essays", False),
            "essay_points_map": exam_config.get("essay_points_map", {}),
            "template_path": exam_config.get("template_path", ""),
            "roi_coordinates": exam_config.get("roi_coordinates", {}),
            "group_name": group_name,
            "roster": self.get_roster(group_name),
            "answer_versions": exam_config.get("answer_versions", [SINGLE_VERSION_KEY]),
            "model_answers": exam_config.get("model_answers", {}),      # {version: {"1": "A", ...}}
            "voided_questions": exam_config.get("voided_questions", {}),  # {version: [q, ...]}
        }

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

        if not config.get("template_path"):
            problems.append("No ROI template has been saved yet.")

        if config.get("has_essays") and not config.get("essay_points_map"):
            problems.append("Essay questions are enabled but have no point values saved.")

        return problems