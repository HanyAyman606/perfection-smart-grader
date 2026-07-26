"""
screens/session_bank_dialog.py
--------------------------------
Themed replacement for a raw QFileDialog.getExistingDirectory when opening
an existing workspace. Shows recently-used projects as cards (the "Session
Bank" from the spec) with a fallback to browse for an untracked folder.
"""

import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QWidget, QFrame, QFileDialog
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import (
    SKY_AQUA, TEXT_MUTED, NEON_PINK, BG_PANEL, BG_CARD, TRUE_AZURE, CLOUDY_SKY, WARN_COLOR
)
from admin_dashboard.recent_projects import recent_projects
from admin_dashboard.widgets.common import apply_card_shadow


class SessionBankDialog(QDialog):
    """On accept(), .selected_path holds the chosen workspace folder."""

    def __init__(self, orbitron, mono, parent=None):
        super().__init__(parent)
        self.orbitron = orbitron
        self.mono = mono
        self.selected_path = None

        self.setWindowTitle("Open Existing Workspace")
        self.setFixedSize(520, 560)
        self.setStyleSheet(f"QDialog {{ background-color: {BG_PANEL}; }}")

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(26, 24, 26, 24)

        title = QLabel("SESSION BANK")
        title.setFont(QFont(orbitron, 14, QFont.Weight.Black))
        title.setStyleSheet(f"color: {CLOUDY_SKY}; letter-spacing: 2px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Recently used exam workspaces")
        subtitle.setFont(QFont(mono, 9))
        subtitle.setStyleSheet(f"color: {TEXT_MUTED};")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        self.list_container = QWidget()
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setSpacing(10)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.list_container)
        layout.addWidget(self.scroll)

        self._populate_list()

        btn_browse = QPushButton("📂 BROWSE FOR OTHER FOLDER")
        btn_browse.setFont(QFont(orbitron, 10, QFont.Weight.Bold))
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent; color: {TEXT_MUTED};
                border: 2px dashed {TEXT_MUTED}; border-radius: 8px; padding: 12px;
            }}
            QPushButton:hover {{ background-color: {TEXT_MUTED}; color: #ffffff; border-style: solid; }}
        """)
        btn_browse.clicked.connect(self._browse_for_folder)
        layout.addWidget(btn_browse)

    def _populate_list(self):
        for i in reversed(range(self.list_layout.count())):
            w = self.list_layout.itemAt(i).widget()
            if w:
                w.setParent(None)

        entries = recent_projects.list_recent()
        if not entries:
            empty_lbl = QLabel("No recent workspaces yet — browse for one below.")
            empty_lbl.setFont(QFont(self.mono, 10))
            empty_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setWordWrap(True)
            self.list_layout.addWidget(empty_lbl)
            return

        for entry in entries:
            self.list_layout.addWidget(self._build_card(entry))

    def _build_card(self, entry: dict) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{ background-color: {BG_CARD}; border: 2px solid {TRUE_AZURE}; border-radius: 12px; }}
            QFrame:hover {{ border: 2px solid {CLOUDY_SKY}; }}
        """)
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 12, 12)

        text_col = QVBoxLayout()
        name_lbl = QLabel(entry["name"].upper())
        name_lbl.setFont(QFont(self.orbitron, 11, QFont.Weight.Bold))
        name_lbl.setStyleSheet(f"color: {SKY_AQUA}; background: transparent; border: none;")
        path_lbl = QLabel(entry["path"])
        path_lbl.setFont(QFont(self.mono, 8))
        path_lbl.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent; border: none;")
        text_col.addWidget(name_lbl)
        text_col.addWidget(path_lbl)
        row.addLayout(text_col)
        row.addStretch()

        btn_open = QPushButton("OPEN")
        btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent; color: {NEON_PINK};
                border: 2px solid {NEON_PINK}; border-radius: 6px; padding: 8px 14px;
            }}
            QPushButton:hover {{ background-color: {NEON_PINK}; color: #ffffff; }}
        """)
        btn_open.clicked.connect(lambda checked, p=entry["path"]: self._select(p))

        btn_remove = QPushButton("✕")
        btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_remove.setFixedWidth(32)
        btn_remove.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {WARN_COLOR}; border: none; }}
            QPushButton:hover {{ background-color: {WARN_COLOR}; color: #ffffff; }}
        """)
        btn_remove.clicked.connect(lambda checked, p=entry["path"], c=card: self._remove_entry(p, c))

        row.addWidget(btn_open)
        row.addWidget(btn_remove)
        apply_card_shadow(card)
        return card

    def _remove_entry(self, path, card_widget):
        recent_projects.remove(path)
        card_widget.setParent(None)

    def _select(self, path):
        self.selected_path = path
        self.accept()

    def _browse_for_folder(self):
        options = QFileDialog.Option.DontUseNativeDialog
        dir_path = QFileDialog.getExistingDirectory(self, "Select Existing Workspace Folder", options=options)
        if dir_path:
            self.selected_path = dir_path
            self.accept()