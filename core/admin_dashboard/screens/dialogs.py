"""
screens/dialogs.py
--------------------
Themed replacements for QMessageBox.information/warning/critical/question
and QFileDialog — matches NewGroupDialog's visual language instead of
falling through to the OS-native dialog.
"""

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import SKY_AQUA, TEXT_MUTED, NEON_PINK, BG_PANEL, TRUE_AZURE, WARN_COLOR
from admin_dashboard.widgets.common import apply_card_shadow


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
    path, _ = QFileDialog.getOpenFileName(
        parent, title, start_dir, file_filter,
        options=QFileDialog.Option.DontUseNativeDialog,
    )
    return path


def save_file_dialog(parent, title, file_filter, start_dir=""):
    path, _ = QFileDialog.getSaveFileName(
        parent, title, start_dir, file_filter,
        options=QFileDialog.Option.DontUseNativeDialog,
    )
    return path


def open_directory_dialog(parent, title):
    return QFileDialog.getExistingDirectory(
        parent, title, options=QFileDialog.Option.DontUseNativeDialog,
    )