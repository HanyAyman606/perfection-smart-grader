"""
screens/new_project_dialog.py
-------------------------------
Themed replacement for QInputDialog.getText + QFileDialog.getExistingDirectory
when creating a new exam workspace. Validates the name and blocks silently
overwriting a folder that already exists (same-named project reused by
accident).
"""

import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import SKY_AQUA, TEXT_MUTED, NEON_PINK, BG_PANEL, TRUE_AZURE, WARN_COLOR, CLOUDY_SKY, INPUT_STYLE
from admin_dashboard.screens.dialogs import open_directory_dialog


class NewProjectDialog(QDialog):
    """On accept(), .project_name and .parent_dir hold the validated result."""

    def __init__(self, orbitron, mono, parent=None):
        super().__init__(parent)
        self.project_name = None
        self.parent_dir = None
        self._chosen_dir = None

        self.setWindowTitle("New Exam Workspace")
        self.setFixedWidth(460)
        self.setStyleSheet(f"QDialog {{ background-color: {BG_PANEL}; }}")

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(30, 26, 30, 26)

        title = QLabel("CREATE NEW WORKSPACE")
        title.setFont(QFont(orbitron, 14, QFont.Weight.Black))
        title.setStyleSheet(f"color: {NEON_PINK}; letter-spacing: 1px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        name_lbl = QLabel("EXAM / PROJECT NAME:")
        name_lbl.setStyleSheet(f"color:{TEXT_MUTED}; font-size:10px;")
        layout.addWidget(name_lbl)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Quiz 67")
        self.name_input.setFont(QFont(orbitron, 12))
        self.name_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.name_input)

        loc_lbl = QLabel("SAVE LOCATION:")
        loc_lbl.setStyleSheet(f"color:{TEXT_MUTED}; font-size:10px;")
        layout.addWidget(loc_lbl)

        loc_row = QHBoxLayout()
        self.location_display = QLineEdit()
        self.location_display.setReadOnly(True)
        self.location_display.setPlaceholderText("No folder selected yet...")
        self.location_display.setStyleSheet(INPUT_STYLE)
        btn_browse = QPushButton("📂 BROWSE")
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {CLOUDY_SKY};
                border: 2px solid {CLOUDY_SKY}; border-radius: 8px; padding: 10px 14px;
            }}
            QPushButton:hover {{ background-color: {CLOUDY_SKY}; color: #000000; }}
        """)
        btn_browse.clicked.connect(self._choose_folder)
        loc_row.addWidget(self.location_display)
        loc_row.addWidget(btn_browse)
        layout.addLayout(loc_row)

        self.error_lbl = QLabel("")
        self.error_lbl.setStyleSheet(f"color: {WARN_COLOR}; font-size: 11px;")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.error_lbl)

        btn_create = QPushButton("✨ CREATE WORKSPACE")
        btn_create.setFont(QFont(orbitron, 11, QFont.Weight.Bold))
        btn_create.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_create.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {NEON_PINK};
                border: 2px solid {NEON_PINK}; border-radius: 8px; padding: 14px;
            }}
            QPushButton:hover {{ background-color: {NEON_PINK}; color: #ffffff; }}
        """)
        btn_create.clicked.connect(self._attempt_create)
        layout.addWidget(btn_create)

    def _choose_folder(self):
        dir_path = open_directory_dialog(self, "Select Directory to Save Project")
        if dir_path:
            self._chosen_dir = dir_path
            self.location_display.setText(dir_path)

    def _attempt_create(self):
        name = self.name_input.text().strip()
        if not name:
            self.error_lbl.setText("Enter a project name.")
            return
        if not self._chosen_dir:
            self.error_lbl.setText("Choose a save location first.")
            return

        target = os.path.join(self._chosen_dir, name.replace(" ", "_"))
        if os.path.exists(target):
            self.error_lbl.setText(f"A workspace named '{name}' already exists there.")
            return

        self.project_name = name
        self.parent_dir = self._chosen_dir
        self.accept()