"""
exam_modes.py
--------------
Registry of exam modes for the blueprint page's mode selector.

A mode is just a stable ID + display label. Adding a new mode later means
adding one entry to EXAM_MODES below — SetupPage, ProjectManager, and the
sync-packet builder all read/store the mode by its stable `id`, never by
array index or hardcoded string compare, so nothing else needs to change.

If a future mode needs its own extra config (e.g. a Shamel-style 4-digit
student ID + an "Exam Day" selector), that becomes a separate mode-specific
config dataclass plugged in alongside ExamMode — not an if/elif branch
inside SetupPage.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExamMode:
    id: str     # stored in config/DB — never rename once used, breaks old sessions
    label: str  # shown in the UI dropdown


EXAM_MODES = [
    ExamMode(id="quiz", label="Quiz Mode"),
    ExamMode(id="exam", label="Exam Mode"),
]

DEFAULT_MODE_ID = EXAM_MODES[0].id


def get_mode_by_id(mode_id: str) -> ExamMode:
    return next((m for m in EXAM_MODES if m.id == mode_id), EXAM_MODES[0])