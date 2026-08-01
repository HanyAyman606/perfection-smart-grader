"""
screens/new_group_dialog.py
----------------------------
Themed replacement for QInputDialog.getText() when creating a group —
matches the app's card/input language instead of the OS-native dialog.
"""

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import SKY_AQUA, TEXT_MUTED, TEXT_FEED, NEON_PINK, BG_PANEL, TRUE_AZURE
from admin_dashboard.widgets.common import apply_card_shadow


class NewGroupDialog(QDialog):
    def __init__(self, orbitron, mono, parent=None, title="New Group", initial_text=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedWidth(420)
        self.setStyleSheet(f"QDialog {{ background-color: {BG_PANEL}; border-radius: 14px; }}")
        apply_card_shadow(self)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(30, 26, 30, 26)

        title_label = QLabel(title.upper())
        title_label.setFont(QFont(orbitron, 14, QFont.Weight.Black))
        title_label.setStyleSheet(f"color: {SKY_AQUA}; letter-spacing: 1px;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        hint = QLabel("GROUP IDENTIFIER (e.g., Sidi Bishr 10 AM):")
        hint.setFont(QFont(mono, 10))
        hint.setStyleSheet(f"color: {TEXT_MUTED};")
        layout.addWidget(hint)

        self.name_input = QLineEdit(initial_text)
        self.name_input.setFont(QFont(mono, 12))
        self.name_input.setPlaceholderText("Group name")
        self.name_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {BG_PANEL}; color: {TEXT_FEED};
                border: 2px solid {TRUE_AZURE}; border-radius: 8px; padding: 10px;
            }}
            QLineEdit:focus {{ border: 2px solid {NEON_PINK}; }}
        """)
        self.name_input.returnPressed.connect(self.accept)
        layout.addWidget(self.name_input)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("✕ CANCEL")
        btn_cancel.setFont(QFont(orbitron, 10, QFont.Weight.Bold))
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {TEXT_MUTED};
                border: 2px solid {TEXT_MUTED}; border-radius: 8px; padding: 12px;
            }}
            QPushButton:hover {{ background-color: {TEXT_MUTED}; color: #ffffff; }}
        """)
        btn_cancel.clicked.connect(self.reject)

        btn_ok = QPushButton("✔ CREATE")
        btn_ok.setFont(QFont(orbitron, 10, QFont.Weight.Bold))
        btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ok.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {SKY_AQUA};
                border: 2px solid {SKY_AQUA}; border-radius: 8px; padding: 12px;
            }}
            QPushButton:hover {{ background-color: {SKY_AQUA}; color: #ffffff; }}
        """)
        btn_ok.clicked.connect(self.accept)

        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

        self.name_input.setFocus()

    @staticmethod
    def get_group_name(orbitron, mono, parent=None, title="New Group", initial_text="") -> tuple[str, bool]:
        """Mirrors QInputDialog.getText's return shape (text, accepted)
        so the call site barely changes."""
        dialog = NewGroupDialog(orbitron, mono, parent, title=title, initial_text=initial_text)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        return dialog.name_input.text().strip(), accepted