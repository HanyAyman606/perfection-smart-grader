"""
roster_repository.py
----------------------
Owns the on-disk `students` table inside a workspace's roster.db — the
same table GradingRepository's `grades`/`sessions` tables live alongside,
but a different concern (who's enrolled vs. what they scored). Pulled out
of ProjectManager, which previously mixed three unrelated persistence
concerns in one class: JSON config I/O, this SQLite roster table, and
Excel export. Static methods taking `db_path` explicitly (rather than an
instance holding it) mirror GradingRepository's own static helpers
(`ensure_grades_schema`, `get_group_grades`) for the same reason: no
per-workspace instance to keep in sync as ProjectManager's active
db_path changes when the admin switches projects.
"""

import os
import sqlite3
from contextlib import contextmanager

STUDENTS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS students (
        student_id TEXT PRIMARY KEY, student_name TEXT NOT NULL,
        is_present INTEGER DEFAULT 1, group_name TEXT
    )
"""


@contextmanager
def _connection(db_path: str):
    """Guarantees conn.close() runs even if a query raises mid-method —
    the previous _connect() returned a bare (conn, cursor) pair with each
    call site responsible for its own conn.close(), which meant an early
    `return` or a raised exception between _connect() and close() leaked
    the connection (see e.g. purge_orphaned_groups' early-return path
    before this fix). Auto-commits on clean exit; a raised exception
    skips the commit and still closes the connection."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(STUDENTS_TABLE_SQL)
    try:
        yield conn, cursor
        conn.commit()
    finally:
        conn.close()


class RosterRepository:
    @staticmethod
    def get_groups(db_path: str) -> list[tuple[str, int]]:
        """Returns [(group_name, student_count), ...] for the group hub cards."""
        if not os.path.exists(db_path):
            return []
        with _connection(db_path) as (conn, cursor):
            cursor.execute("SELECT group_name, COUNT(*) FROM students GROUP BY group_name")
            rows = cursor.fetchall()
        return [(name, count) for name, count in rows if name]

    @staticmethod
    def purge_orphaned_groups(db_path: str, known_group_names: set[str]):
        """Self-healing safety net: strips any roster/grade/session rows for
        group names that no longer exist in the global registry — covers
        workspaces that fell out of recent_projects' history (or were moved)
        before a global delete could reach them. Mirrors
        CrossWorkspaceGroupSync.purge_group_everywhere()'s table coverage
        so an orphaned group can't leave grade history behind just because
        it was cleaned up this way instead of via explicit delete.

        Takes known_group_names as a plain set rather than reaching for
        group_registry itself, so this class stays a pure SQL layer with
        no dependency on the app's registry singleton — the caller (which
        already knows about workspace-open policy) supplies the source of
        truth."""
        if not os.path.exists(db_path):
            return

        with _connection(db_path) as (conn, cursor):
            cursor.execute("SELECT DISTINCT group_name FROM students WHERE group_name IS NOT NULL")
            stored_names = {row[0] for row in cursor.fetchall()}

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('grades', 'sessions')"
            )
            existing_tables = {row[0] for row in cursor.fetchall()}
            if "sessions" in existing_tables:
                cursor.execute("SELECT DISTINCT group_name FROM sessions WHERE group_name IS NOT NULL")
                stored_names |= {row[0] for row in cursor.fetchall()}

            orphans = stored_names - known_group_names
            if not orphans:
                return

            for name in orphans:
                cursor.execute("DELETE FROM students WHERE group_name = ?", (name,))
                if "sessions" in existing_tables and "grades" in existing_tables:
                    cursor.execute(
                        "DELETE FROM grades WHERE session_id IN "
                        "(SELECT session_id FROM sessions WHERE group_name = ?)",
                        (name,),
                    )
                if "sessions" in existing_tables:
                    cursor.execute("DELETE FROM sessions WHERE group_name = ?", (name,))