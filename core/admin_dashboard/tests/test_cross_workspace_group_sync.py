"""
tests/test_cross_workspace_group_sync.py
--------------------------------------------
Covers Phase 1 of REFACTOR_PLAN.md — Finding 1, for CrossWorkspaceGroupSync.
See test_grading_repository.py's module docstring for the general
approach (functional regression + a forced-exception leak check).

recent_projects.list_recent() is monkeypatched rather than touching the
real global registry, so these tests don't depend on or mutate
whatever the actual application's recent-projects list currently holds.
"""
import os
import sqlite3

import pytest

from admin_dashboard.cross_workspace_group_sync import CrossWorkspaceGroupSync
from admin_dashboard import cross_workspace_group_sync as sync_module


@pytest.fixture
def workspace(tmp_path):
    proj_dir = tmp_path / "proj1"
    proj_dir.mkdir()
    db_path = str(proj_dir / "roster.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE students (
            student_id TEXT PRIMARY KEY, student_name TEXT NOT NULL,
            is_present INTEGER DEFAULT 1, group_name TEXT
        )
    """)
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, group_name TEXT)")
    conn.execute("CREATE TABLE grades (scan_id INTEGER PRIMARY KEY, session_id TEXT)")
    conn.execute("INSERT INTO students VALUES ('1', 'Alice', 1, 'GroupA')")
    conn.execute("INSERT INTO sessions VALUES ('s1', 'GroupA')")
    conn.execute("INSERT INTO grades VALUES (1, 's1')")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture(autouse=True)
def patch_recent_projects(monkeypatch, workspace):
    fake_entries = [{"path": os.path.dirname(workspace), "name": "proj1"}]
    monkeypatch.setattr(
        sync_module.recent_projects, "list_recent", lambda: fake_entries
    )


# ----------------------------------------------------------------------
# Functional regression
# ----------------------------------------------------------------------

def test_rename_group_everywhere_updates_students_and_sessions(workspace):
    CrossWorkspaceGroupSync.rename_group_everywhere("GroupA", "GroupA-Renamed")

    conn = sqlite3.connect(workspace)
    student_group = conn.execute("SELECT group_name FROM students WHERE student_id='1'").fetchone()[0]
    session_group = conn.execute("SELECT group_name FROM sessions WHERE session_id='s1'").fetchone()[0]
    conn.close()

    assert student_group == "GroupA-Renamed"
    assert session_group == "GroupA-Renamed"


def test_purge_group_everywhere_removes_students_sessions_and_grades(workspace):
    CrossWorkspaceGroupSync.purge_group_everywhere("GroupA")

    conn = sqlite3.connect(workspace)
    assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM grades").fetchone()[0] == 0
    conn.close()


def test_purge_skips_workspaces_missing_the_db_file(tmp_path, monkeypatch):
    """A workspace in recent_projects whose roster.db no longer exists on
    disk (moved/deleted folder) must be skipped, not raise."""
    ghost_dir = tmp_path / "ghost_proj"
    ghost_dir.mkdir()  # folder exists, but no roster.db inside it
    monkeypatch.setattr(
        sync_module.recent_projects, "list_recent",
        lambda: [{"path": str(ghost_dir), "name": "ghost"}],
    )
    CrossWorkspaceGroupSync.purge_group_everywhere("GroupA")  # should not raise


# ----------------------------------------------------------------------
# The actual leak, forced and disproven
# ----------------------------------------------------------------------

def test_connection_closes_after_exception_in_rename(monkeypatch, workspace):
    """A non-string old_name breaks sqlite3 parameter binding with a real
    ProgrammingError. Asserts the connection opened for that workspace
    was still closed afterward. Fails against the pre-fix code (bare
    conn.close() after conn.commit(), unreachable once execute() raises)."""
    real_connect = sqlite3.connect
    captured = {}

    def spy_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        captured["conn"] = conn
        return conn

    monkeypatch.setattr(sqlite3, "connect", spy_connect)

    with pytest.raises(sqlite3.ProgrammingError):
        CrossWorkspaceGroupSync.rename_group_everywhere(old_name=object(), new_name="X")

    leaked_conn = captured["conn"]
    with pytest.raises(sqlite3.ProgrammingError):
        leaked_conn.execute("SELECT 1")