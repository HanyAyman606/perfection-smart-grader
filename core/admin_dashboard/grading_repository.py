"""
grading_repository.py
-----------------------
Owns the `sessions` and `grades` tables inside the ACTIVE WORKSPACE's
roster.db (the same file ProjectManager already uses for `students`) —
NOT the old standalone grading_system.db from db_manager.py, which was a
disconnected prototype that never got wired into the real per-project
workspace model. Keeping grades in roster.db means one project = one
self-contained folder (nexus_project.json + roster.db), matching every
other piece of ProjectManager's design.

Schema:
  sessions(session_id PK, group_name, answer_version, started_at)
    - One row per "Start Live Grading" click. session_id is a fresh
      value each time (see new_session_id()) so re-starting a session
      for the same group doesn't collide with a previous run's grades.
  grades(scan_id PK, session_id, student_id, mcq_score, essay_total,
         final_score, answer_version, mistakes_log, timestamp)
    - UNIQUE(session_id, student_id) is the duplicate-scan guard the
      WebSocket server checks before inserting.
    - answer_version is a REAL column (not folded into mistakes_log's
      JSON) so it's queryable/exportable on its own — matters for
      Shamel mode's per-booklet auditing.
"""

import sqlite3
import json
import uuid
from datetime import datetime
from typing import Optional


SESSIONS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        group_name TEXT NOT NULL,
        answer_version TEXT,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

GRADES_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS grades (
        scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        student_id TEXT NOT NULL,
        mcq_score REAL NOT NULL,
        essay_total REAL DEFAULT 0,
        final_score REAL NOT NULL,
        answer_version TEXT,
        mistakes_log TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES sessions (session_id),
        UNIQUE(session_id, student_id)
    )
"""


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


class GradingRepository:
    """One instance per live session. db_path is ProjectManager.db_path
    (the active workspace's roster.db) — pass it in rather than importing
    ProjectManager here, so this stays unit-testable without Qt."""

    def __init__(self, db_path: str, session_id: str):
        self.db_path = db_path
        self.session_id = session_id
        self._ensure_tables()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_tables(self):
        conn = self._connect()
        conn.execute(SESSIONS_TABLE_SQL)
        conn.execute(GRADES_TABLE_SQL)
        conn.commit()
        conn.close()

    def start_session(self, group_name: str):
        """Call once when 'Start Live Grading' is confirmed — registers
        this session_id so grades have a valid FK target."""
        conn = self._connect()
        conn.execute(
            "INSERT OR IGNORE INTO sessions (session_id, group_name) VALUES (?, ?)",
            (self.session_id, group_name),
        )
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    def get_existing_grade(self, student_id: str) -> Optional[dict]:
        conn = self._connect()
        cursor = conn.execute(
            "SELECT final_score, answer_version, mistakes_log, timestamp "
            "FROM grades WHERE session_id = ? AND student_id = ?",
            (self.session_id, student_id),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        final_score, answer_version, mistakes_log, timestamp = row
        return {
            "score": final_score,
            "answer_version": answer_version,
            "mistakes": json.loads(mistakes_log) if mistakes_log else [],
            "timestamp": timestamp,
        }

    def save_grade(self, student_id: str, mcq_score: float, essay_total: float,
                    total_score: float, mistakes: list, answer_version: Optional[str]):
        conn = self._connect()
        conn.execute(
            "INSERT INTO grades (session_id, student_id, mcq_score, essay_total, "
            "final_score, answer_version, mistakes_log) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.session_id, student_id, mcq_score, essay_total, total_score,
             answer_version, json.dumps(mistakes)),
        )
        conn.commit()
        conn.close()

    def overwrite_grade(self, student_id: str, mcq_score: float, essay_total: float,
                         total_score: float, mistakes: list, answer_version: Optional[str]):
        conn = self._connect()
        conn.execute(
            "UPDATE grades SET mcq_score=?, essay_total=?, final_score=?, "
            "answer_version=?, mistakes_log=?, timestamp=CURRENT_TIMESTAMP "
            "WHERE session_id=? AND student_id=?",
            (mcq_score, essay_total, total_score, answer_version, json.dumps(mistakes),
             self.session_id, student_id),
        )
        conn.commit()
        conn.close()

    def discard_grade(self, student_id: str):
        conn = self._connect()
        conn.execute(
            "DELETE FROM grades WHERE session_id = ? AND student_id = ?",
            (self.session_id, student_id),
        )
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    def get_session_grades(self) -> list[dict]:
        """Used by export_group_results / a future results view — every
        saved grade for this session, newest first."""
        conn = self._connect()
        cursor = conn.execute(
            "SELECT student_id, mcq_score, essay_total, final_score, answer_version, "
            "mistakes_log, timestamp FROM grades WHERE session_id = ? ORDER BY timestamp DESC",
            (self.session_id,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "student_id": r[0], "mcq_score": r[1], "essay_total": r[2],
                "final_score": r[3], "answer_version": r[4],
                "mistakes": json.loads(r[5]) if r[5] else [],
                "timestamp": r[6],
            }
            for r in rows
        ]