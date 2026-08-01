"""
screens/session_password_dialog.py
-------------------------------------
Lets the admin view/change the CURRENT WORKSPACE's phone-session
password (what proctors type into the Flutter app to connect) — separate
from auth_manager.py's admin-login password. Stored per-project via
ProjectManager.get_session_password()/set_session_password(), so
different exams can use different session passwords if desired.
"""

from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import SKY_AQUA, TEXT_MUTED, NEON_PINK, BG_PANEL, TRUE_AZURE, WARN_COLOR, INPUT_STYLE

MIN_SESSION_PASSWORD_LENGTH = 4


class SessionPasswordDialog(QDialog):
    def __init__(self, project_manager, orbitron, mono, parent=None):
        super().__init__(parent)
        self.project_manager = project_manager
        self.setWindowTitle("Phone Session Password")
        self.setFixedWidth(420)
        self.setStyleSheet(f"QDialog {{ background-color: {BG_PANEL}; }}")

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(30, 26, 30, 26)

        title = QLabel("PHONE SESSION PASSWORD")
        title.setFont(QFont(orbitron, 14, QFont.Weight.Black))
        title.setStyleSheet(f"color: {SKY_AQUA}; letter-spacing: 1px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("This is what proctors type into the mobile app to connect \u2014 separate from your admin login password.")
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont(mono, 9))
        subtitle.setStyleSheet(f"color: {TEXT_MUTED};")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        self.password_input = QLineEdit()
        self.password_input.setText(self.project_manager.get_session_password())
        self.password_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.password_input)

        self.error_lbl = QLabel("")
        self.error_lbl.setFont(QFont(mono, 10))
        self.error_lbl.setStyleSheet(f"color: {WARN_COLOR};")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.error_lbl)

        btn_save = QPushButton("SAVE PASSWORD")
        btn_save.setFont(QFont(orbitron, 11, QFont.Weight.Bold))
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {NEON_PINK};
                border: 2px solid {NEON_PINK}; border-radius: 8px; padding: 12px;
            }}
            QPushButton:hover {{ background-color: {NEON_PINK}; color: #ffffff; }}
        """)
        btn_save.clicked.connect(self._save)
        layout.addWidget(btn_save)

    def _save(self):
        new_password = self.password_input.text().strip()
        if len(new_password) < MIN_SESSION_PASSWORD_LENGTH:
            self.error_lbl.setText(f"Password must be at least {MIN_SESSION_PASSWORD_LENGTH} characters.")
            return

        self.project_manager.set_session_password(new_password)
        self.error_lbl.setStyleSheet(f"color: {SKY_AQUA};")
        self.error_lbl.setText("Saved. Takes effect next time you start a live session.")
