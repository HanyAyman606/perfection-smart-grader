"""
workspace_controller.py
------------------------
Owns "which workspace is open and how did we get there": creating a new
project, opening an existing one via the file browser, or quick-opening a
recent one. Pulled out of CyberpunkDashboard, which previously did this
*and* built every pixel of the topbar/sidebar/content chrome in the same
class — two responsibilities that don't need to change together (a tweak
to how "New Project" validates a name has nothing to do with how the
sidebar is laid out).

This class intentionally knows nothing about QMainWindow, the sidebar, or
nav buttons — just ProjectManager and the two dialogs involved in getting
a workspace open. The dashboard chrome reacts to the result via the
on_activated callback instead of this class reaching into dashboard
internals, so either side can change independently.
"""

from PySide6.QtWidgets import QDialog

from admin_dashboard.project_manager import CONFIG_FILENAME
from admin_dashboard.screens.new_project_dialog import NewProjectDialog
from admin_dashboard.screens.session_bank_dialog import SessionBankDialog
from admin_dashboard.screens.dialogs import show_error


class WorkspaceController:
    """`on_activated` is called with no arguments once a workspace is
    successfully created or opened — the dashboard wires this to
    `activate_dashboard` to switch the visible screen and refresh chrome
    that depends on the now-active project (e.g. the sidebar's workspace
    name label)."""

    def __init__(self, project_manager, fonts, on_activated):
        self.project_manager = project_manager
        self.fonts = fonts
        self.on_activated = on_activated

    def create_new_project(self, parent):
        dialog = NewProjectDialog(self.fonts.orbitron, self.fonts.mono, parent=parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.project_manager.create_project(dialog.project_name, dialog.parent_dir)
        self.on_activated()

    def open_existing_project(self, parent):
        dialog = SessionBankDialog(self.fonts.orbitron, self.fonts.mono, parent=parent)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_path:
            return
        self.quick_open_project(dialog.selected_path, parent)

    def quick_open_project(self, path: str, parent):
        if self.project_manager.open_project(path):
            self.on_activated()
        else:
            show_error(parent, self.fonts.orbitron, self.fonts.mono, "Error",
                       f"Invalid Workspace: {CONFIG_FILENAME} not found in this folder.")