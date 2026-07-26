"""
recent_projects.py
-------------------
Tracks recently created/opened workspace folders so "Open Existing" can
offer a Session Bank (pick from a list) instead of a raw folder browse
every single time.

Design notes mirror auth_manager.py:
- Single-responsibility: only knows how to read/write the recent-projects
  list. UI (SessionBankDialog) never touches the JSON directly.
- Singleton-ish usage: import `recent_projects` (module-level instance),
  don't instantiate RecentProjectsManager yourself.
- Storage: <project_root>/config/recent_projects.json
    [{"name": "Quiz 67", "path": "/abs/path", "last_opened": "iso-timestamp"}]
  Not sensitive — pure UX convenience data. Entries pointing at folders
  that no longer exist are pruned automatically on read.
"""

import os
import json
from datetime import datetime

MAX_ENTRIES = 12


class RecentProjectsManager:
    def __init__(self, config_path: str = None):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_path = config_path or os.path.join(base_dir, "config", "recent_projects.json")

    def _load(self) -> list[dict]:
        if not os.path.exists(self.config_path):
            return []
        try:
            with open(self.config_path, "r") as f:
                entries = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        return [e for e in entries if os.path.isdir(e.get("path", ""))]

    def _save(self, entries: list[dict]):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump(entries, f, indent=2)

    def list_recent(self) -> list[dict]:
        """Newest-first, pruned of missing folders."""
        entries = self._load()
        entries.sort(key=lambda e: e.get("last_opened", ""), reverse=True)
        return entries

    def touch(self, name: str, path: str):
        """Adds/updates an entry and bumps it to the top of the list."""
        entries = [e for e in self._load() if os.path.abspath(e["path"]) != os.path.abspath(path)]
        entries.insert(0, {"name": name, "path": path, "last_opened": datetime.now().isoformat()})
        self._save(entries[:MAX_ENTRIES])

    def remove(self, path: str):
        entries = [e for e in self._load() if os.path.abspath(e["path"]) != os.path.abspath(path)]
        self._save(entries)


recent_projects = RecentProjectsManager()