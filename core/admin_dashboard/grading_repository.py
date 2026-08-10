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
        group_type TEXT,
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
        self._ensure_group_type_column(conn)
        conn.commit()
        conn.close()

    @staticmethod
    def ensure_grades_schema(db_path: str):
        """Callable without a session_id — for read paths (like export)
        that need the schema current but aren't starting a grading session."""
        conn = sqlite3.connect(db_path)
        conn.execute(SESSIONS_TABLE_SQL)
        conn.execute(GRADES_TABLE_SQL)
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(grades)")}
        if "group_type" not in existing_cols:
            conn.execute("ALTER TABLE grades ADD COLUMN group_type TEXT")
        conn.commit()
        conn.close()

    def _ensure_group_type_column(self, conn):
        """Migration for grades tables created before group_type existed —
        CREATE TABLE IF NOT EXISTS alone won't add a column to a table
        that's already there."""
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(grades)")}
        if "group_type" not in existing_cols:
            conn.execute("ALTER TABLE grades ADD COLUMN group_type TEXT")

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
                    total_score: float, mistakes: list, answer_version: Optional[str],
                    group_type: Optional[str] = None):
        conn = self._connect()
        conn.execute(
            "INSERT INTO grades (session_id, student_id, mcq_score, essay_total, "
            "final_score, answer_version, group_type, mistakes_log) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (self.session_id, student_id, mcq_score, essay_total, total_score,
             answer_version, group_type, json.dumps(mistakes)),
        )
        conn.commit()
        conn.close()

    def overwrite_grade(self, student_id: str, mcq_score: float, essay_total: float,
                         total_score: float, mistakes: list, answer_version: Optional[str],
                         group_type: Optional[str] = None):
        conn = self._connect()
        conn.execute(
            "UPDATE grades SET mcq_score=?, essay_total=?, final_score=?, "
            "answer_version=?, group_type=?, mistakes_log=?, timestamp=CURRENT_TIMESTAMP "
            "WHERE session_id=? AND student_id=?",
            (mcq_score, essay_total, total_score, answer_version, group_type, json.dumps(mistakes),
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

    @staticmethod
    def get_group_grades(db_path: str, group_name: str) -> list[dict]:
        """Every grade ever saved for this group's sessions, sorted by
        group_type first (M's together, then N's, then W's...) since
        one grading session's location can contain students from several
        administrative groups — group_type is a per-student value read
        off their sheet, not the same thing as the session's group_name."""
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT g.student_id, g.group_type, g.answer_version, g.mcq_score, "
            "g.essay_total, g.final_score, g.timestamp "
            "FROM grades g JOIN sessions s ON g.session_id = s.session_id "
            "WHERE s.group_name = ? "
            "ORDER BY g.group_type IS NULL, g.group_type, g.student_id",
            (group_name,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "student_id": r[0], "group_type": r[1], "answer_version": r[2],
                "mcq_score": r[3], "essay_total": r[4], "final_score": r[5], "timestamp": r[6],
            }
            for r in rows
        ]

    @staticmethod
    def delete_group_grades(db_path: str, group_name: str) -> int:
        """Wipes every grade ever saved for this group, across all its
        sessions — the DB-level counterpart to a fresh Excel export
        containing only whatever a *new* live grading session produces.
        Leaves the sessions/students rows themselves alone; only the
        grades rows are removed. Returns the number of rows deleted."""
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "DELETE FROM grades WHERE session_id IN "
            "(SELECT session_id FROM sessions WHERE group_name = ?)",
            (group_name,),
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return deleted

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