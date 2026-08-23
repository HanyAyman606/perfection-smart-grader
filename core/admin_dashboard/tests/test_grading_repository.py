"""
tests/test_grading_repository.py
-----------------------------------
Covers Phase 1 of REFACTOR_PLAN.md — Finding 1 (un-closed DB connections
on exception).

Two kinds of coverage here:
  1. Functional regression — save/read/overwrite/discard/export paths
     still behave correctly after the conn = connect(); ...; conn.close()
     pattern was replaced with a context manager.
  2. The actual bug being fixed — forces a real sqlite exception mid-
     method (a genuine UNIQUE constraint violation) and asserts the
     connection was still closed. This test is written to FAIL against
     the pre-fix save_grade() (no try/finally around conn.close()) and
     PASS against the fixed version, same convention as
     test_websocket_Server.py's Phase-1 tests.
"""
import sqlite3

import pytest

from admin_dashboard.grading_repository import GradingRepository, new_session_id


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "roster.db")


@pytest.fixture
def repo(db_path):
    r = GradingRepository(db_path, new_session_id())
    r.start_session("GroupA")
    return r


# ----------------------------------------------------------------------
# Functional regression
# ----------------------------------------------------------------------

def test_save_and_read_grade(repo):
    repo.save_grade("S001", mcq_score=8.0, essay_total=2.0, total_score=10.0,
                     mistakes=[1, 2], answer_version="A")
    existing = repo.get_existing_grade("S001")
    assert existing["score"] == 10.0
    assert existing["mistakes"] == [1, 2]


def test_overwrite_grade(repo):
    repo.save_grade("S001", mcq_score=8.0, essay_total=2.0, total_score=10.0,
                     mistakes=[1, 2], answer_version="A")
    repo.overwrite_grade("S001", mcq_score=9.0, essay_total=2.0, total_score=11.0,
                          mistakes=[1], answer_version="A")
    existing = repo.get_existing_grade("S001")
    assert existing["score"] == 11.0
    assert existing["mistakes"] == [1]


def test_discard_grade(repo):
    repo.save_grade("S001", mcq_score=8.0, essay_total=2.0, total_score=10.0,
                     mistakes=[], answer_version="A")
    repo.discard_grade("S001")
    assert repo.get_existing_grade("S001") is None


def test_get_group_grades(db_path, repo):
    repo.save_grade("S001", mcq_score=8.0, essay_total=2.0, total_score=10.0,
                     mistakes=[], answer_version="A")
    grades = GradingRepository.get_group_grades(db_path, "GroupA")
    assert len(grades) == 1
    assert grades[0]["student_id"] == "S001"


def test_get_existing_grade_in_group_spans_sessions(db_path):
    """A student graded in an earlier session for the same group must
    still be caught as a duplicate by a repo instance running a new
    session_id — this is the whole point of get_existing_grade_in_group
    existing separately from get_existing_grade."""
    old_repo = GradingRepository(db_path, new_session_id())
    old_repo.start_session("GroupA")
    old_repo.save_grade("S001", mcq_score=8.0, essay_total=2.0, total_score=10.0,
                         mistakes=[], answer_version="A")

    new_repo = GradingRepository(db_path, new_session_id())
    new_repo.start_session("GroupA")
    found = new_repo.get_existing_grade_in_group("GroupA", "S001")
    assert found is not None
    assert found["score"] == 10.0
    # But the fresh session's own get_existing_grade shouldn't see it —
    # different session_id.
    assert new_repo.get_existing_grade("S001") is None


def test_get_session_grades(repo):
    repo.save_grade("S001", mcq_score=8.0, essay_total=2.0, total_score=10.0,
                     mistakes=[], answer_version="A")
    repo.save_grade("S002", mcq_score=7.0, essay_total=1.0, total_score=8.0,
                     mistakes=[3], answer_version="A")
    rows = repo.get_session_grades()
    assert {r["student_id"] for r in rows} == {"S001", "S002"}


def test_delete_group_grades(db_path, repo):
    repo.save_grade("S001", mcq_score=8.0, essay_total=2.0, total_score=10.0,
                     mistakes=[], answer_version="A")
    deleted = GradingRepository.delete_group_grades(db_path, "GroupA")
    assert deleted == 1
    assert GradingRepository.get_group_grades(db_path, "GroupA") == []


def test_ensure_grades_schema_adds_missing_column(db_path):
    """Simulates an old roster.db created before group_type existed —
    ensure_grades_schema must add the column without erroring on a
    table that's already there."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE grades (
            scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, student_id TEXT NOT NULL,
            mcq_score REAL NOT NULL, essay_total REAL DEFAULT 0,
            final_score REAL NOT NULL, answer_version TEXT,
            mistakes_log TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

    GradingRepository.ensure_grades_schema(db_path)

    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(grades)")}
    conn.close()
    assert "group_type" in cols


# ----------------------------------------------------------------------
# The actual leak, forced and disproven
# ----------------------------------------------------------------------

def test_connection_closes_after_exception_mid_save(monkeypatch, repo):
    """Forces a real UNIQUE(session_id, student_id) constraint violation
    inside save_grade() and asserts the connection it opened was still
    closed. Fails against the pre-fix code (bare conn.close() after
    conn.commit(), unreachable once execute() raises)."""
    real_connect = sqlite3.connect
    captured = {}

    def spy_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        captured["conn"] = conn
        return conn

    monkeypatch.setattr(sqlite3, "connect", spy_connect)

    repo.save_grade("DUPTEST", mcq_score=1.0, essay_total=0, total_score=1.0,
                     mistakes=[], answer_version="A")

    with pytest.raises(sqlite3.IntegrityError):
        repo.save_grade("DUPTEST", mcq_score=1.0, essay_total=0, total_score=1.0,
                         mistakes=[], answer_version="A")

    leaked_conn = captured["conn"]
    with pytest.raises(sqlite3.ProgrammingError):
        leaked_conn.execute("SELECT 1")  # only raises if the connection was actually closed