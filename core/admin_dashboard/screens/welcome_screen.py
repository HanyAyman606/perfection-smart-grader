import os
import sys

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QMessageBox
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QDesktopServices

from admin_dashboard.theme import (
    CLOUDY_SKY, NEON_PINK, SKY_AQUA, BG_CARD, TEXT_MUTED, TRUE_AZURE, ELECTRIC_SAPPHIRE
)
from admin_dashboard.screens.change_password_dialog import ChangePasswordDialog
from admin_dashboard.recent_projects import recent_projects


def _get_app_base_dir():
    """
    Directory that contains the 'admin_dashboard' package/assets at runtime.

    Nuitka standalone/onefile still writes real files to disk at runtime —
    onefile unpacks its payload into a temp extraction folder and then runs
    from there — so this module's own __file__ correctly points inside that
    temp folder when compiled, same as it points into the source tree when
    running plain 'python main.py'. That makes it a reliable base in both
    cases, unlike __compiled__ (only reliably present on __main__, and not
    even consistently there depending on Nuitka version) or sys.executable
    (which for onefile is the small launcher stub's own folder, e.g. dist/,
    NOT the temp folder its payload was actually extracted into).
    """
    file_based = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(file_based, "admin_dashboard", "assets")):
        return file_based

    # Fallbacks, in case __file__ ever doesn't line up (e.g. a future
    # Nuitka layout change) — try __compiled__ off __main__, then the
    # executable's own folder, before giving up and returning the
    # __file__-based guess anyway so callers still get *something*.
    compiled = getattr(sys.modules.get("__main__"), "__compiled__", None)
    if compiled is not None:
        candidate = compiled.containing_dir
        if os.path.exists(os.path.join(candidate, "admin_dashboard", "assets")):
            return candidate
    if getattr(sys, "frozen", False):
        candidate = os.path.dirname(sys.executable)
        if os.path.exists(os.path.join(candidate, "admin_dashboard", "assets")):
            return candidate
    return file_based


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
        if not os.path.exists(STUDIO_HTML_PATH):
            QMessageBox.warning(
                self, "Bubble Sheet Studio",
                f"Could not find bubble_sheet_studio.html.\nLooked at:\n{STUDIO_HTML_PATH}",
            )
            return

        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(STUDIO_HTML_PATH))
        if not ok:
            # Fallback: bypass Qt's URL dispatch and ask the OS shell directly.
            try:
                if sys.platform.startswith("win"):
                    os.startfile(STUDIO_HTML_PATH)  # type: ignore[attr-defined]
                else:
                    import subprocess
                    opener = "open" if sys.platform == "darwin" else "xdg-open"
                    subprocess.Popen([opener, STUDIO_HTML_PATH])
            except Exception as e:
                QMessageBox.warning(
                    self, "Bubble Sheet Studio",
                    f"Failed to open the browser for:\n{STUDIO_HTML_PATH}\n\n{e}",
                )

    def _open_change_password_dialog(self):
        dialog = ChangePasswordDialog(self.orbitron, self.mono, parent=self)
        dialog.exec()