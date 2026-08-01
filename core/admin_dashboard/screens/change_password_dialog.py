"""
screens/change_password_dialog.py
-----------------------------------
Modal dialog for changing the admin password from within the app.
Reuses AuthManager.verify()/set_password() as-is — no changes needed there.
"""

from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import SKY_AQUA, TEXT_MUTED, NEON_PINK, BG_PANEL, TRUE_AZURE, WARN_COLOR
from admin_dashboard.auth_manager import auth_manager

MIN_PASSWORD_LENGTH = 6


class ChangePasswordDialog(QDialog):
    def __init__(self, orbitron, mono, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Change Admin Password")
        self.setFixedWidth(420)
        self.setStyleSheet(f"QDialog {{ background-color: {BG_PANEL}; }}")

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(30, 26, 30, 26)

        title = QLabel("CHANGE ADMIN PASSWORD")
        title.setFont(QFont(orbitron, 14, QFont.Weight.Black))
        title.setStyleSheet(f"color: {SKY_AQUA}; letter-spacing: 1px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.current_input = self._make_field("Current Password")
        self.new_input = self._make_field("New Password")
        self.confirm_input = self._make_field("Confirm New Password")

        layout.addWidget(QLabel("CURRENT PASSWORD:", styleSheet=f"color:{TEXT_MUTED}; font-size:10px;"))
        layout.addWidget(self.current_input)
        layout.addWidget(QLabel("NEW PASSWORD:", styleSheet=f"color:{TEXT_MUTED}; font-size:10px;"))
        layout.addWidget(self.new_input)
        layout.addWidget(QLabel("CONFIRM NEW PASSWORD:", styleSheet=f"color:{TEXT_MUTED}; font-size:10px;"))
        layout.addWidget(self.confirm_input)

        self.error_lbl = QLabel("")
        self.error_lbl.setFont(QFont(mono, 10))
        self.error_lbl.setStyleSheet(f"color: {WARN_COLOR};")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.error_lbl)

        btn_confirm = QPushButton("UPDATE PASSWORD")
        btn_confirm.setFont(QFont(orbitron, 11, QFont.Weight.Bold))
        btn_confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_confirm.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {SKY_AQUA};
                border: 2px solid {SKY_AQUA}; border-radius: 8px; padding: 12px;
            }}
            QPushButton:hover {{ background-color: {SKY_AQUA}; color: #000000; }}
        """)
        btn_confirm.clicked.connect(self._attempt_change)
        layout.addWidget(btn_confirm)

    def _make_field(self, placeholder):
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setStyleSheet(f"""
            QLineEdit {{
                background-color: #0d0819; color: {NEON_PINK};
                border: 2px solid {TRUE_AZURE}; border-radius: 8px; padding: 10px;
            }}
            QLineEdit:focus {{ border: 2px solid {NEON_PINK}; }}
        """)
        return field

    def _attempt_change(self):
        current = self.current_input.text()
        new = self.new_input.text()
        confirm = self.confirm_input.text()

        if not auth_manager.verify(current):
            self.error_lbl.setText("Current password is incorrect.")
            return
        if len(new) < MIN_PASSWORD_LENGTH:
            self.error_lbl.setText(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")
            return
        if new != confirm:
            self.error_lbl.setText("New password and confirmation do not match.")
            return

        auth_manager.set_password(new)
        self.error_lbl.setStyleSheet(f"color: {SKY_AQUA};")
        self.error_lbl.setText("Password updated successfully.")
        self.current_input.clear()
        self.new_input.clear()
        self.confirm_input.clear()
