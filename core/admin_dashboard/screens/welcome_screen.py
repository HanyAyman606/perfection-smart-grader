import os

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QDesktopServices

from admin_dashboard.theme import (
    CLOUDY_SKY, NEON_PINK, SKY_AQUA, BG_CARD, TEXT_MUTED, TRUE_AZURE, ELECTRIC_SAPPHIRE
)
from admin_dashboard.screens.change_password_dialog import ChangePasswordDialog
from admin_dashboard.recent_projects import recent_projects

STUDIO_HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "bubble_sheet_studio.html",
)


class WelcomeScreen(QWidget):
    def __init__(self, new_proj_cb, open_proj_cb, quick_open_cb, orbitron, mono):
        super().__init__()
        self.orbitron = orbitron
        self.mono = mono
        self.quick_open_cb = quick_open_cb

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(24)

        title = QLabel("WORKSPACE HUB")
        title.setFont(QFont(orbitron, 28, QFont.Weight.Black))
        title.setStyleSheet(f"color: {CLOUDY_SKY}; letter-spacing: 2px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # placeholder — populated by refresh_last_session_card()
        self.last_session_card = None
        self.last_session_slot = QVBoxLayout()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(20)

        btn_new = QPushButton("+ NEW EXAM WORKSPACE")
        btn_new.setFont(QFont(orbitron, 12, QFont.Weight.Bold))
        btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_new.setFixedSize(300, 100)
        btn_new.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD}; color: {NEON_PINK};
                border: 2px solid {NEON_PINK}; border-radius: 12px;
            }}
            QPushButton:hover {{ background-color: {NEON_PINK}; color: #ffffff; }}
        """)
        btn_new.clicked.connect(new_proj_cb)

        btn_open = QPushButton("📂 OPEN EXISTING WORKSPACE")
        btn_open.setFont(QFont(orbitron, 12, QFont.Weight.Bold))
        btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open.setFixedSize(300, 100)
        btn_open.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD}; color: {CLOUDY_SKY};
                border: 2px solid {CLOUDY_SKY}; border-radius: 12px;
            }}
            QPushButton:hover {{ background-color: {CLOUDY_SKY}; color: #000000; }}
        """)
        btn_open.clicked.connect(open_proj_cb)

        btn_studio = QPushButton("🖨 BUBBLE SHEET STUDIO")
        btn_studio.setFont(QFont(orbitron, 12, QFont.Weight.Bold))
        btn_studio.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_studio.setFixedSize(300, 100)
        btn_studio.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD}; color: {ELECTRIC_SAPPHIRE};
                border: 2px solid {ELECTRIC_SAPPHIRE}; border-radius: 12px;
            }}
            QPushButton:hover {{ background-color: {ELECTRIC_SAPPHIRE}; color: #ffffff; }}
        """)
        btn_studio.clicked.connect(self.open_bubble_studio)

        btn_row.addStretch()
        btn_row.addWidget(btn_new)
        btn_row.addWidget(btn_open)
        btn_row.addWidget(btn_studio)
        btn_row.addStretch()

        btn_change_pw = QPushButton("🔒 CHANGE PASSWORD")
        btn_change_pw.setFont(QFont(orbitron, 10, QFont.Weight.Bold))
        btn_change_pw.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_change_pw.setFixedWidth(220)
        btn_change_pw.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent; color: {TEXT_MUTED};
                border: 2px solid {TEXT_MUTED}; border-radius: 8px; padding: 10px;
            }}
            QPushButton:hover {{ background-color: {TEXT_MUTED}; color: #ffffff; }}
        """)
        btn_change_pw.clicked.connect(self._open_change_password_dialog)

        layout.addStretch()
        layout.addWidget(title)
        layout.addLayout(self.last_session_slot)
        layout.addLayout(btn_row)
        layout.addWidget(btn_change_pw, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()

        self.refresh_last_session_card()

    def refresh_last_session_card(self):
        """Called by the dashboard every time this screen becomes active,
        so a project created/opened elsewhere still shows up as 'last'."""
        if self.last_session_card is not None:
            self.last_session_slot.removeWidget(self.last_session_card)
            self.last_session_card.setParent(None)
            self.last_session_card = None

        entries = recent_projects.list_recent()
        if not entries:
            return

        latest = entries[0]
        card = QFrame()
        card.setFixedWidth(460)
        card.setStyleSheet(f"""
            QFrame {{ background-color: {BG_CARD}; border: 2px solid {SKY_AQUA}; border-radius: 14px; }}
        """)
        row = QHBoxLayout(card)
        row.setContentsMargins(20, 14, 14, 14)

        text_col = QVBoxLayout()
        lbl_tag = QLabel("CONTINUE LAST SESSION")
        lbl_tag.setFont(QFont(self.mono, 8, QFont.Weight.Bold))
        lbl_tag.setStyleSheet(f"color: {TEXT_MUTED}; letter-spacing: 1px; background: transparent; border: none;")
        lbl_name = QLabel(latest["name"].upper())
        lbl_name.setFont(QFont(self.orbitron, 13, QFont.Weight.Bold))
        lbl_name.setStyleSheet(f"color: {SKY_AQUA}; background: transparent; border: none;")
        text_col.addWidget(lbl_tag)
        text_col.addWidget(lbl_name)
        row.addLayout(text_col)
        row.addStretch()

        btn_continue = QPushButton("CONTINUE ▶")
        btn_continue.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_continue.setFont(QFont(self.orbitron, 10, QFont.Weight.Bold))
        btn_continue.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent; color: {SKY_AQUA};
                border: 2px solid {SKY_AQUA}; border-radius: 8px; padding: 10px 16px;
            }}
            QPushButton:hover {{ background-color: {SKY_AQUA}; color: #000000; }}
        """)
        btn_continue.clicked.connect(lambda: self.quick_open_cb(latest["path"]))
        row.addWidget(btn_continue)

        self.last_session_card = card
        self.last_session_slot.addWidget(card, alignment=Qt.AlignmentFlag.AlignHCenter)

    def open_bubble_studio(self):
        """Bubble Sheet Studio is a standalone tool that lives outside any
        workspace — it launches in the system's default browser, same as
        it used to from inside the dashboard, just reachable from the hub
        now instead of a sidebar page."""
        if os.path.exists(STUDIO_HTML_PATH):
            QDesktopServices.openUrl(QUrl.fromLocalFile(STUDIO_HTML_PATH))

    def _open_change_password_dialog(self):
        dialog = ChangePasswordDialog(self.orbitron, self.mono, parent=self)
        dialog.exec()