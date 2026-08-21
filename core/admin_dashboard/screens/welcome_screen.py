import os
import sys

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QDesktopServices

from admin_dashboard.theme import (
    CLOUDY_SKY, NEON_PINK, SKY_AQUA, BG_CARD, TEXT_MUTED, TRUE_AZURE, ELECTRIC_SAPPHIRE
)
from admin_dashboard.screens.change_password_dialog import ChangePasswordDialog
from admin_dashboard.recent_projects import recent_projects
from admin_dashboard.widgets.styled import make_title_label, make_outline_button


def _get_app_base_dir():
    """
    Directory that contains the 'admin_dashboard' package/assets at runtime.

    - Nuitka (--standalone / --onefile): __compiled__ is injected as a
      module-level global and its .containing_dir points at the directory
      the running binary was extracted/unpacked into (NOT the source tree).
    - Normal 'python main.py' execution: fall back to walking up from this
      file's location on disk, same as before.
    """
    try:
        return __compiled__.containing_dir  # type: ignore[name-defined]
    except NameError:
        pass
    if getattr(sys, "frozen", False):
        # generic fallback for other freezers (PyInstaller, cx_Freeze, etc.)
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


STUDIO_HTML_PATH = os.path.join(
    _get_app_base_dir(), "admin_dashboard", "assets", "bubble_sheet_studio.html",
)
if not os.path.exists(STUDIO_HTML_PATH):
    # Some Nuitka layouts place the package contents directly at the
    # extraction root rather than nested under admin_dashboard/. Try that
    # layout too before giving up, so this doesn't silently 404 in the UI.
    _alt = os.path.join(_get_app_base_dir(), "assets", "bubble_sheet_studio.html")
    if os.path.exists(_alt):
        STUDIO_HTML_PATH = _alt


class WelcomeScreen(QWidget):
    def __init__(self, new_proj_cb, open_proj_cb, quick_open_cb, orbitron, mono):
        super().__init__()
        self.orbitron = orbitron
        self.mono = mono
        self.quick_open_cb = quick_open_cb

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(24)

        title = make_title_label("WORKSPACE HUB", orbitron, CLOUDY_SKY, font_size=28)

        # placeholder — populated by refresh_last_session_card()
        self.last_session_card = None
        self.last_session_slot = QVBoxLayout()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(20)

        btn_new = make_outline_button("+ NEW EXAM WORKSPACE", orbitron, NEON_PINK, font_size=12, background=BG_CARD, radius=12)
        btn_new.setFixedSize(300, 100)
        btn_new.clicked.connect(new_proj_cb)

        btn_open = make_outline_button("📂 OPEN EXISTING WORKSPACE", orbitron, CLOUDY_SKY, font_size=12, background=BG_CARD, radius=12, hover_text_color="#000000")
        btn_open.setFixedSize(300, 100)
        btn_open.clicked.connect(open_proj_cb)

        btn_studio = make_outline_button("🖨 BUBBLE SHEET STUDIO", orbitron, ELECTRIC_SAPPHIRE, font_size=12, background=BG_CARD, radius=12)
        btn_studio.setFixedSize(300, 100)
        btn_studio.clicked.connect(self.open_bubble_studio)

        btn_row.addStretch()
        btn_row.addWidget(btn_new)
        btn_row.addWidget(btn_open)
        btn_row.addWidget(btn_studio)
        btn_row.addStretch()

        btn_change_pw = make_outline_button("🔒 CHANGE PASSWORD", orbitron, TEXT_MUTED, font_size=10, background="transparent", padding="10px")
        btn_change_pw.setFixedWidth(220)
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

        btn_continue = make_outline_button("CONTINUE ▶", self.orbitron, SKY_AQUA, font_size=10, background="transparent", padding="10px 16px", hover_text_color="#000000")
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