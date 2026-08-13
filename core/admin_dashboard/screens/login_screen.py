from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import SKY_AQUA, TEXT_MUTED, NEON_PINK, BG_PANEL, TRUE_AZURE, WARN_COLOR
from admin_dashboard.auth_manager import auth_manager


class LoginScreen(QWidget):
    def __init__(self, auth_callback, orbitron, mono):
        super().__init__()
        self.auth_callback = auth_callback

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(20)

        brand_lbl = QLabel("OPTIMARK")
        brand_lbl.setFont(QFont(orbitron, 36, QFont.Weight.Black))
        brand_lbl.setStyleSheet(f"color: {SKY_AQUA}; letter-spacing: 5px;")
        brand_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub_lbl = QLabel("SECURE ADMIN TERMINAL")
        sub_lbl.setFont(QFont(mono, 12))
        sub_lbl.setStyleSheet(f"color: {TEXT_MUTED}; letter-spacing: 2px;")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Enter Admin Password...")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setFont(QFont(orbitron, 14))
        self.password_input.setFixedWidth(400)
        self.password_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {BG_PANEL}; color: {NEON_PINK};
                border: 2px solid {TRUE_AZURE}; border-radius: 8px; padding: 15px;
            }}
            QLineEdit:focus {{ border: 2px solid {NEON_PINK}; }}
        """)
        self.password_input.returnPressed.connect(self.attempt_login)

        btn_login = QPushButton("AUTHENTICATE")
        btn_login.setFont(QFont(orbitron, 12, QFont.Weight.Bold))
        btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_login.setFixedWidth(400)
        btn_login.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {SKY_AQUA};
                border: 2px solid {SKY_AQUA}; border-radius: 8px; padding: 15px;
            }}
            QPushButton:hover {{ background-color: {SKY_AQUA}; color: #000000; }}
        """)
        btn_login.clicked.connect(self.attempt_login)

        self.error_lbl = QLabel("")
        self.error_lbl.setFont(QFont(mono, 10))
        self.error_lbl.setStyleSheet(f"color: {WARN_COLOR};")
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        layout.addWidget(brand_lbl)
        layout.addWidget(sub_lbl)
        layout.addSpacing(40)
        layout.addWidget(self.password_input, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(btn_login, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.error_lbl)
        layout.addStretch()

    def attempt_login(self):
        if auth_manager.verify(self.password_input.text()):
            self.error_lbl.setText("")
            self.auth_callback()
        else:
            self.error_lbl.setText("ACCESS DENIED: Invalid Credentials")
            self.password_input.clear()
