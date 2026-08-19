"""
sync_packet_builder.py
------------------------
Builds the JSON payload sent to mobile clients over the grading socket,
extracted out of ProjectManager.build_sync_packet().

Same rationale as blueprint_layout_engine.py: this is packet-shaping
logic, not persistence. It takes an already-loaded config dict (and the
handful of other values it needs) rather than a ProjectManager instance
or a file path, so it can be unit-tested against a plain dict literal —
no workspace directory, no JSON file on disk required.
"""

from admin_dashboard.blueprint_layout_engine import BlueprintLayoutEngine
from admin_dashboard.exam_modes import DEFAULT_MODE_ID, SINGLE_VERSION_KEY, get_mode_by_id


class SyncPacketBuilder:
    @staticmethod
    def build(exam_config: dict, group_name: str, fallback_project_name: str | None = None) -> dict:
        mode_id = exam_config.get("mode", DEFAULT_MODE_ID)
        exam_mode = get_mode_by_id(mode_id)
        mcq_count = exam_config.get("mcq_count", 0)

        if exam_mode.has_answer_versions:
            mcq_columns = BlueprintLayoutEngine.compute_fixed_fill(mcq_count, num_cols=6, col_size=10)
        else:
            mcq_columns = BlueprintLayoutEngine.compute_even_split(mcq_count)

        return {
            "exam_name": exam_config.get("project_name", fallback_project_name),
            "exam_mode": mode_id,
            "mcq_count": mcq_count,
            "mcq_ranges": exam_config.get("mcq_ranges", []),
            "has_essays": exam_config.get("has_essays", False),
            "essay_points_map": exam_config.get("essay_points_map", {}),
            "group_name": group_name,
            "answer_versions": exam_config.get("answer_versions", [SINGLE_VERSION_KEY]),
            "model_answers": exam_config.get("model_answers", {}),  # {version: {"1": "A", ...}}
            "voided_questions": exam_config.get("voided_questions", {}),  # {version: [q, ...]}
            "choices_per_question": exam_config.get("choices_per_question", 4),
            "mcq_columns": mcq_columns,  # {"num_cols": n, "columns": {"1": n, ...}}
            "id": {
                "num_digits": exam_mode.id_digit_count,  # derived from mode, not saved
                "num_letters": len(exam_config.get("id_letters", "CDEFMW")),
                "letters": list(exam_config.get("id_letters", "CDEFMW")),
            },
        }