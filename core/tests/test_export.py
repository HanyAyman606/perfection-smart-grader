"""
Quick manual test for export_group_to_excel — no Qt, no server, no phone.
Fabricates grades/sessions rows directly, then checks the Excel output.
Run: python test_export.py
"""

import os
import shutil
import sqlite3
import tempfile

from admin_dashboard.grading_repository import GradingRepository, new_session_id
from admin_dashboard.project_manager import ProjectManager


def make_mock_workspace(tmp_dir):
    pm = ProjectManager()
    pm.create_project("Mock Exam", tmp_dir)

    # Two sessions for the same group — exercises the "all sessions,
    # combined" export scope we settled on earlier.
    for session_num in range(2):
        repo = GradingRepository(pm.db_path, new_session_id())
        repo.start_session("Sidi Beshr")

        mock_rows = [
            # (student_id, mcq, essay, total, answer_version, group_type)
            ("2001", 8.0, 2.0, 10.0, "A", "M"),
            ("2002", 6.0, 1.0, 7.0, "A", "N"),
            ("2003", 9.0, 2.0, 11.0, "A", "M"),
            ("2004", 5.0, 0.0, 5.0, "A", None),   # simulates a scan with no group_type yet
        ]
        for i, (sid, mcq, essay, total, version, group_type) in enumerate(mock_rows):
            # unique student_id per session so UNIQUE(session_id, student_id) doesn't collide
            repo.save_grade(
                student_id=f"{sid}-s{session_num}", mcq_score=mcq, essay_total=essay,
                total_score=total, mistakes=[{"q": 3, "given": "B", "correct": "C"}],
                answer_version=version, group_type=group_type,
            )

    return pm


def main():
    tmp_dir = tempfile.mkdtemp(prefix="nexus_export_test_")
    try:
        pm = make_mock_workspace(tmp_dir)
        out_path = os.path.join(tmp_dir, "export_test.xlsx")
        pm.export_group_to_excel("Sidi Beshr", out_path)

        import pandas as pd
        df = pd.read_excel(out_path)
        print(df.to_string(index=False))

        # Sanity checks
        assert len(df) == 8, f"expected 8 rows (2 sessions x 4 students), got {len(df)}"
        assert list(df["Group Type"])[-2:] == [None, None] or df["Group Type"].isna().sum() == 2, \
            "NULL group_type rows should sort to the end"
        print("\n✔ Export test passed.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()