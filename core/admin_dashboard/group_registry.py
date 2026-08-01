"""
group_registry.py
------------------
Global (cross-workspace) registry of group identifiers — e.g. "Sidi Bishr 10 AM".

Unlike roster data (students, counts), which is intentionally scoped to a
single project's roster.db, the *set of group names* is meant to be shared
by every exam session — so it lives outside any project folder, mirroring
recent_projects.py's storage convention.

Design:
- Single Responsibility: this class only persists/validates the name list.
  It knows nothing about Qt widgets, rosters, or ProjectManager.
- Observer pattern via Qt signals: any number of pages can subscribe to
  group_added / group_removed and stay in sync without the registry
  knowing they exist (Open/Closed — new subscribers need no change here).
- Singleton-ish usage: import `group_registry` (module instance), don't
  instantiate GroupRegistry yourself — this guarantees every page reacts
  to the same signals.
"""

import os
import json

from PySide6.QtCore import QObject, Signal


class GroupRegistry(QObject):
    group_added = Signal(str)
    group_removed = Signal(str)
    group_renamed = Signal(str, str)

    def __init__(self, config_path: str = None):
        super().__init__()
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_path = config_path or os.path.join(base_dir, "config", "groups.json")

    def _load(self) -> list[str]:
        if not os.path.exists(self.config_path):
            return []
        try:
            with open(self.config_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, names: list[str]):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump(names, f, indent=2)

    def list_groups(self) -> list[str]:
        """Alphabetical — stable ordering for the hub grid."""
        return sorted(self._load(), key=str.lower)

    def add_group(self, name: str) -> bool:
        """Returns False if the name already exists (case-insensitive) —
        caller decides how to react (e.g. open it instead of duplicating)."""
        names = self._load()
        if any(existing.lower() == name.lower() for existing in names):
            return False
        names.append(name)
        self._save(names)
        self.group_added.emit(name)
        return True

    def remove_group(self, name: str):
        names = [n for n in self._load() if n != name]
        self._save(names)
        self.group_removed.emit(name)

    def rename_group(self, old_name: str, new_name: str) -> bool:
        """Returns False if old_name isn't found or new_name collides
        (case-insensitive) with a different existing group."""
        names = self._load()
        if old_name not in names:
            return False
        if any(n.lower() == new_name.lower() and n != old_name for n in names):
            return False

        names = [new_name if n == old_name else n for n in names]
        self._save(names)
        self.group_renamed.emit(old_name, new_name)
        return True


group_registry = GroupRegistry()