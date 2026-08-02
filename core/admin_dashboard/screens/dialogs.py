"""
screens/dialogs.py
--------------------
Themed replacements for QMessageBox.information/warning/critical/question
and QFileDialog — matches NewGroupDialog's visual language instead of
falling through to the OS-native dialog.
"""

import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QListView, QLineEdit, QAbstractItemView,QFileSystemModel
)
from PySide6.QtCore import Qt, QDir, QSize
from PySide6.QtGui import QFont

from admin_dashboard.theme import SKY_AQUA, TEXT_MUTED, NEON_PINK, BG_PANEL, BG_CARD, TRUE_AZURE, WARN_COLOR, CLOUDY_SKY,TEXT_FEED
from admin_dashboard.widgets.common import apply_card_shadow


class ThemedFileBrowserDialog(QDialog):
    """Replaces Qt's own file/directory browser with one that actually
    matches the app's theme — a card grid instead of a native table view.
    One class, three modes, since the navigation/browsing logic (walk
    into folders, go up, style the grid) is identical across all three;
    only what "Choose" does at the end differs.
    """

    def __init__(self, parent, title, mode: str, name_filter="All Files (*)",
                 start_dir="", suggested_name=""):
        super().__init__(parent)
        self.mode = mode  # "open_file" | "save_file" | "select_directory"
        self._chosen_path = None
        self.current_dir = start_dir or QDir.homePath()

        self.setWindowTitle(title)
        self.setFixedSize(640, 520)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #f0f9ff;
                border-radius: 14px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 22, 24, 20)

        title_lbl = QLabel(title.upper())
        title_lbl.setStyleSheet(f"color: {SKY_AQUA}; font-weight: 900; letter-spacing: 1px; font-size: 14px;")
        layout.addWidget(title_lbl)

        nav_row = QHBoxLayout()
        btn_up = QPushButton("⬆ UP")
        btn_up.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_up.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_PANEL}; color: {TEXT_MUTED};
            border: 2px solid {TEXT_MUTED}; border-radius: 6px; padding: 6px 12px; }}
            QPushButton:hover {{ background-color: {TEXT_MUTED}; color: #ffffff; }}
        """)
        btn_up.clicked.connect(self._go_up)
        self.path_label = QLabel(self.current_dir)
        self.path_label.setStyleSheet(f"color: {TEXT_MUTED}; font-family: monospace; font-size: 11px;")
        nav_row.addWidget(btn_up)
        nav_row.addWidget(self.path_label, stretch=1)
        layout.addLayout(nav_row)

        self.model = QFileSystemModel()
        self.model.setRootPath(QDir.rootPath())
        if mode == "select_directory":
            self.model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        else:
            filters = self._parse_filter(name_filter)
            if filters:
                self.model.setNameFilters(filters)
                self.model.setNameFilterDisables(False)  # hide non-matching instead of graying out

        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setRootIndex(self.model.index(self.current_dir))
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setGridSize(QSize(112, 92))
        self.view.setSpacing(14)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setStyleSheet(f"""
            QListView {{
                background-color: transparent; color: {TEXT_MUTED};
                border: 2px solid {TRUE_AZURE}; border-radius: 10px; padding: 8px;
            }}
            QListView::item {{
                border: 2px solid {TRUE_AZURE}; border-radius: 10px; padding: 4px;
                color: {TEXT_FEED}; background-color: rgba(59,130,246,0.06);
            }}
            QListView::item:hover {{
                border: 2px solid {CLOUDY_SKY}; background-color: rgba(56,189,248,0.15);
            }}
            QListView::item:selected {{
                border: 2px solid {NEON_PINK}; background-color: rgba(236,72,153,0.15); color: {NEON_PINK};
            }}
        """)
        self.view.doubleClicked.connect(self._on_double_click)
        self.view.clicked.connect(self._on_click)
        layout.addWidget(self.view, stretch=1)

        self.filename_input = None
        if mode == "save_file":
            self.filename_input = QLineEdit(suggested_name)
            self.filename_input.setStyleSheet(f"""
                QLineEdit {{ background-color: {BG_CARD}; color: {TEXT_MUTED};
                border: 2px solid {TRUE_AZURE}; border-radius: 8px; padding: 10px; }}
                QLineEdit:focus {{ border: 2px solid {NEON_PINK}; }}
            """)
            self.filename_input.textChanged.connect(self._update_confirm_enabled)
            layout.addWidget(self.filename_input)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("✕ CANCEL")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_PANEL}; color: {TEXT_MUTED};
            border: 2px solid {TEXT_MUTED}; border-radius: 8px; padding: 12px; }}
            QPushButton:hover {{ background-color: {TEXT_MUTED}; color: #ffffff; }}
        """)
        btn_cancel.clicked.connect(self.reject)

        confirm_label = {"open_file": "✔ OPEN", "save_file": "💾 SAVE", "select_directory": "✔ CHOOSE THIS FOLDER"}[mode]
        self.btn_confirm = QPushButton(confirm_label)
        self.btn_confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_confirm.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_PANEL}; color: {SKY_AQUA};
            border: 2px solid {SKY_AQUA}; border-radius: 8px; padding: 12px; }}
            QPushButton:hover {{ background-color: {SKY_AQUA}; color: #ffffff; }}
            QPushButton:disabled {{ background-color: {BG_PANEL}; color: {TEXT_MUTED}; border-color: {TEXT_MUTED}; }}
        """)
        self.btn_confirm.clicked.connect(self._confirm)
        self._update_confirm_enabled()

        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self.btn_confirm)
        layout.addLayout(btn_row)

    @staticmethod
    def _parse_filter(qt_filter_string: str) -> list[str]:
        """'Images (*.png *.jpg *.jpeg)' -> ['*.png', '*.jpg', '*.jpeg']"""
        if "(" not in qt_filter_string:
            return []
        inside = qt_filter_string.split("(", 1)[1].rstrip(")")
        return inside.split()

    def _navigate_to(self, path: str):
        self.current_dir = path
        self.view.setRootIndex(self.model.index(path))
        self.path_label.setText(path)
        self._update_confirm_enabled()

    def _go_up(self):
        d = QDir(self.current_dir)
        if d.cdUp():
            self._navigate_to(d.absolutePath())

    def _on_double_click(self, index):
        path = self.model.filePath(index)
        if self.model.isDir(index):
            self._navigate_to(path)
        elif self.mode == "open_file":
            self._chosen_path = path
            self.accept()

    def _on_click(self, index):
        if self.model.isDir(index):
            return
        if self.mode == "open_file":
            self._chosen_path = self.model.filePath(index)
            self._update_confirm_enabled()
        elif self.mode == "save_file":
            self.filename_input.setText(self.model.fileName(index))

    def _update_confirm_enabled(self):
        if self.mode == "open_file":
            self.btn_confirm.setEnabled(self._chosen_path is not None)
        elif self.mode == "save_file":
            self.btn_confirm.setEnabled(bool(self.filename_input.text().strip()))
        else:  # select_directory — the current folder itself is always a valid choice
            self.btn_confirm.setEnabled(True)

    def _confirm(self):
        if self.mode == "select_directory":
            self._chosen_path = self.current_dir
        elif self.mode == "save_file":
            name = self.filename_input.text().strip()
            if not name:
                return
            self._chosen_path = os.path.join(self.current_dir, name)
        self.accept()

    def result_path(self) -> str:
        return self._chosen_path or ""


class _ThemedMessageDialog(QDialog):
    def __init__(self, orbitron, mono, title, message, accent, parent=None, confirm_mode=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedWidth(420)
        self.setStyleSheet(f"QDialog {{ background-color: {BG_PANEL}; border-radius: 14px; }}")
        apply_card_shadow(self)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(30, 26, 30, 26)

        title_lbl = QLabel(title.upper())
        title_lbl.setFont(QFont(orbitron, 14, QFont.Weight.Black))
        title_lbl.setStyleSheet(f"color: {accent}; letter-spacing: 1px;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_lbl)

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setFont(QFont(mono, 10))
        msg_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(msg_lbl)

        btn_row = QHBoxLayout()
        if confirm_mode:
            btn_no = QPushButton("✕ CANCEL")
            btn_no.setFont(QFont(orbitron, 10, QFont.Weight.Bold))
            btn_no.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_no.setStyleSheet(f"""
                QPushButton {{ background-color: {BG_PANEL}; color: {TEXT_MUTED};
                border: 2px solid {TEXT_MUTED}; border-radius: 8px; padding: 12px; }}
                QPushButton:hover {{ background-color: {TEXT_MUTED}; color: #ffffff; }}
            """)
            btn_no.clicked.connect(self.reject)
            btn_row.addWidget(btn_no)

        btn_yes = QPushButton("✔ CONFIRM" if confirm_mode else "✔ OK")
        btn_yes.setFont(QFont(orbitron, 10, QFont.Weight.Bold))
        btn_yes.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_yes.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_PANEL}; color: {accent};
            border: 2px solid {accent}; border-radius: 8px; padding: 12px; }}
            QPushButton:hover {{ background-color: {accent}; color: #ffffff; }}
        """)
        btn_yes.clicked.connect(self.accept)
        btn_row.addWidget(btn_yes)

        layout.addLayout(btn_row)
        btn_yes.setFocus()


def show_info(parent, orbitron, mono, title, message):
    _ThemedMessageDialog(orbitron, mono, title, message, SKY_AQUA, parent).exec()


def show_warning(parent, orbitron, mono, title, message):
    _ThemedMessageDialog(orbitron, mono, title, message, NEON_PINK, parent).exec()


def show_error(parent, orbitron, mono, title, message):
    _ThemedMessageDialog(orbitron, mono, title, message, WARN_COLOR, parent).exec()


def ask_yes_no(parent, orbitron, mono, title, message) -> bool:
    dialog = _ThemedMessageDialog(orbitron, mono, title, message, TRUE_AZURE, parent, confirm_mode=True)
    return dialog.exec() == QDialog.DialogCode.Accepted


def open_file_dialog(parent, title, file_filter, start_dir=""):
    dialog = ThemedFileBrowserDialog(parent, title, mode="open_file", name_filter=file_filter, start_dir=start_dir)
    return dialog.result_path() if dialog.exec() == QDialog.DialogCode.Accepted else ""


def save_file_dialog(parent, title, file_filter, default_name=""):
    dialog = ThemedFileBrowserDialog(parent, title, mode="save_file", name_filter=file_filter, suggested_name=default_name)
    return dialog.result_path() if dialog.exec() == QDialog.DialogCode.Accepted else ""


def open_directory_dialog(parent, title):
    dialog = ThemedFileBrowserDialog(parent, title, mode="select_directory")
    return dialog.result_path() if dialog.exec() == QDialog.DialogCode.Accepted else ""