"""
tests/test_roster_repository.py
-----------------------------------
Covers Phase 1 of REFACTOR_PLAN.md — Finding 1, for RosterRepository.
See test_grading_repository.py's module docstring for the general
approach (functional regression + a forced-exception leak check).
"""
import sqlite3

import pytest

from admin_dashboard.roster_repository import RosterRepository


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "roster.db")


def _seed_students(db_path, rows):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            student_id TEXT PRIMARY KEY, student_name TEXT NOT NULL,
            is_present INTEGER DEFAULT 1, group_name TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO students VALUES (?, ?, 1, ?)",
        rows,
    )
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# Functional regression
# ----------------------------------------------------------------------

def test_get_groups_on_missing_db_returns_empty_without_creating_file(db_path):
    assert RosterRepository.get_groups(db_path) == []
    import os
    assert not os.path.exists(db_path)


def test_get_groups_counts_per_group(db_path):
    _seed_students(db_path, [
        ("1", "Alice", "GroupA"),
        ("2", "Bob", "GroupA"),
        ("3", "Carl", "GroupB"),
    ])
    groups = sorted(RosterRepository.get_groups(db_path))
    assert groups == [("GroupA", 2), ("GroupB", 1)]


def test_purge_orphaned_groups_removes_only_unknown_groups(db_path):
    _seed_students(db_path, [
        ("1", "Alice", "GroupA"),
        ("2", "Bob", "GroupB"),
    ])
    RosterRepository.purge_orphaned_groups(db_path, known_group_names={"GroupA"})
    remaining = RosterRepository.get_groups(db_path)
    assert remaining == [("GroupA", 1)]


def test_purge_orphaned_groups_also_cleans_sessions_and_grades(db_path):
    _seed_students(db_path, [("1", "Alice", "Orphan")])
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, group_name TEXT)")
    conn.execute("CREATE TABLE grades (scan_id INTEGER PRIMARY KEY, session_id TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('s1', 'Orphan')")
    conn.execute("INSERT INTO grades VALUES (1, 's1')")
    conn.commit()
    conn.close()

    RosterRepository.purge_orphaned_groups(db_path, known_group_names=set())

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM grades").fetchone()[0] == 0
    conn.close()


def test_purge_orphaned_groups_no_op_when_nothing_orphaned(db_path):
    """Exercises the early-return path inside the 'with' block — this is
    exactly the branch that used to close the connection explicitly
    before returning; worth its own test to confirm that refactor didn't
    change the no-op behavior."""
    _seed_students(db_path, [("1", "Alice", "GroupA")])
    RosterRepository.purge_orphaned_groups(db_path, known_group_names={"GroupA"})
    assert RosterRepository.get_groups(db_path) == [("GroupA", 1)]


# ----------------------------------------------------------------------
# The actual leak, forced and disproven
# ----------------------------------------------------------------------

def test_connection_closes_after_exception_in_purge(monkeypatch, db_path):
    """Passing None for known_group_names breaks the set-difference op
    inside purge_orphaned_groups with a real TypeError. Asserts the
    connection opened for this call was still closed afterward. Fails
    against the pre-fix code (conn.close() calls scattered through the
    method body, several unreachable once an exception is raised)."""
    _seed_students(db_path, [("1", "Alice", "GroupA")])

    real_connect = sqlite3.connect
    captured = {}

    def spy_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        captured["conn"] = conn
        return conn

    monkeypatch.setattr(sqlite3, "connect", spy_connect)

    with pytest.raises(TypeError):
        RosterRepository.purge_orphaned_groups(db_path, known_group_names=None)

    leaked_conn = captured["conn"]
    with pytest.raises(sqlite3.ProgrammingError):
        leaked_conn.execute("SELECT 1")