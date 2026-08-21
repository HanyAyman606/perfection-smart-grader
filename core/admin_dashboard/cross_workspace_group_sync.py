"""
cross_workspace_group_sync.py
-------------------------------
Group identity (names) is global via group_registry, but each group's
roster/grade rows live inside whichever workspace(s) they were imported
into. When a group is renamed or deleted, that change has to propagate
into every workspace's roster.db, not just the currently active one.

Split out from ProjectManager on purpose: every other method on that
class operates on self.project_dir (the one active workspace). These
operate on *all* workspaces recent_projects knows about — a different
enough responsibility to warrant its own small class (SRP) rather than
living as static methods bolted onto the active-workspace manager.
"""

import os
import sqlite3
from contextlib import contextmanager

from admin_dashboard.recent_projects import recent_projects
from admin_dashboard.project_manager import DB_FILENAME


@contextmanager
def _connection(db_path: str):
    """Guarantees conn.close() runs even if a query raises mid-loop over
    the previous conn = connect(); ...; conn.commit(); conn.close()
    pattern, a raised exception on one workspace's connection (mid-loop,
    across purge_group_everywhere/rename_group_everywhere's iteration
    over every known workspace) skipped conn.close() for that connection
    entirely. Auto-commits on clean exit; a raised exception skips the
    commit and still closes the connection."""
    conn = sqlite3.connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


class CrossWorkspaceGroupSync:
    @staticmethod
    def purge_group_everywhere(group_name: str):
        """Deletes every roster row AND every grade/session tagged with
        group_name from every workspace roster.db we know about. Grades
        are deleted via a session_id subquery since the grades table
        doesn't carry group_name directly — it's reached through sessions."""
        for entry in recent_projects.list_recent():
            db_path = os.path.join(entry["path"], DB_FILENAME)
            if not os.path.exists(db_path):
                continue
            with _connection(db_path) as conn:
                existing_tables = {
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name IN ('students', 'grades', 'sessions')"
                    )
                }
                if "students" in existing_tables:
                    conn.execute("DELETE FROM students WHERE group_name = ?", (group_name,))
                if "sessions" in existing_tables and "grades" in existing_tables:
                    conn.execute(
                        "DELETE FROM grades WHERE session_id IN "
                        "(SELECT session_id FROM sessions WHERE group_name = ?)",
                        (group_name,),
                    )
                if "sessions" in existing_tables:
                    conn.execute("DELETE FROM sessions WHERE group_name = ?", (group_name,))

    @staticmethod
    def rename_group_everywhere(old_name: str, new_name: str):
        """Same reach as purge_group_everywhere, but UPDATEs group_name
        instead of deleting rows — covers students AND sessions (grades
        are keyed by session_id, not group_name, so they follow
        automatically once their parent session is renamed)."""
        for entry in recent_projects.list_recent():
            db_path = os.path.join(entry["path"], DB_FILENAME)
            if not os.path.exists(db_path):
                continue
            with _connection(db_path) as conn:
                existing_tables = {
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name IN ('students', 'sessions')"
                    )
                }
                if "students" in existing_tables:
                    conn.execute(
                        "UPDATE students SET group_name = ? WHERE group_name = ?",
                        (new_name, old_name),
                    )
                if "sessions" in existing_tables:
                    conn.execute(
                        "UPDATE sessions SET group_name = ? WHERE group_name = ?",
                        (new_name, old_name),
                    )