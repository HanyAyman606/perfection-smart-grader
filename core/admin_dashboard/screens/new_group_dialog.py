"""
screens/new_group_dialog.py
----------------------------
Themed replacement for QInputDialog.getText() when creating a group —
matches the app's card/input language instead of the OS-native dialog.
"""

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import SKY_AQUA, TEXT_MUTED, TEXT_FEED, NEON_PINK, BG_PANEL, TRUE_AZURE
from admin_dashboard.widgets.common import apply_card_shadow
from admin_dashboard.widgets.styled import make_title_label, make_outline_button


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

        title_label = make_title_label(title.upper(), orbitron, SKY_AQUA, font_size=14)
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
        btn_cancel = make_outline_button("✕ CANCEL", orbitron, TEXT_MUTED)
        btn_cancel.clicked.connect(self.reject)

        btn_ok = make_outline_button("✔ CREATE", orbitron, SKY_AQUA)
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