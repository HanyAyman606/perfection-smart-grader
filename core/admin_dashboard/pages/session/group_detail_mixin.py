"""
pages/session/group_detail_mixin.py
--------------------------------------
Screen 1 of SessionManagerPage: a single group's detail view — readiness
checklist, export/clear/password-set buttons, and the "start live grading"
button. See group_hub_mixin.py's docstring for why this file exists.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import (
    CLOUDY_SKY, SKY_AQUA, TEXT_MUTED, BG_PANEL, TRUE_AZURE, ELECTRIC_SAPPHIRE, WARN_COLOR, NEON_PINK,
)
from admin_dashboard.pages.base import build_page_shell
from admin_dashboard.widgets.common import ThemedButton
from admin_dashboard.screens.session_password_dialog import SessionPasswordDialog
from admin_dashboard.screens.dialogs import show_warning


class GroupDetailMixin:
    """Screen 1: group detail / readiness / entry point into live grading.
    Requires self.fonts, self.sub_stack, self.project_manager,
    self.active_group_name, and (from LiveMonitorMixin) self.start_live_grading_session,
    self.export_group_results, self.clear_group_results."""

    def _build_group_detail_ui(self):
        page = QWidget()
        content_layout = build_page_shell(page, "Group Management", CLOUDY_SKY, self.fonts.orbitron)

        header_row = QHBoxLayout()
        btn_back = QPushButton("◀ BACK TO GROUPS")
        btn_back.setFont(QFont(self.fonts.orbitron, 10, QFont.Weight.Bold))
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; border: none; }} "
            f"QPushButton:hover {{ background-color: {TEXT_MUTED}; color: #ffffff; }}"
        )
        btn_back.clicked.connect(lambda: self.sub_stack.setCurrentIndex(0))

        self.detail_group_title = QLabel("GROUP_NAME")
        self.detail_group_title.setFont(QFont(self.fonts.orbitron, 16, QFont.Weight.Black))
        self.detail_group_title.setStyleSheet(f"color: {SKY_AQUA};")

        header_row.addWidget(btn_back)
        header_row.addStretch()
        header_row.addWidget(self.detail_group_title)
        content_layout.addLayout(header_row)

        readiness_label = QLabel("SESSION READINESS")
        readiness_label.setFont(QFont(self.fonts.orbitron, 11, QFont.Weight.Bold))
        readiness_label.setStyleSheet(f"color: {TEXT_MUTED}; letter-spacing: 1px;")
        content_layout.addWidget(readiness_label)

        self.readiness_list = QListWidget()
        self.readiness_list.setStyleSheet(f"""
                   QListWidget {{
                       background-color: {BG_PANEL}; color: {TEXT_MUTED};
                       border: 2px solid {TRUE_AZURE}; border-radius: 12px; padding: 10px;
                   }}
                   QListWidget::item {{ padding: 6px 4px; }}
               """)
        content_layout.addWidget(self.readiness_list, stretch=1)

        detail_export_row = QHBoxLayout()

        self.btn_export_detail = ThemedButton(
            "📊 EXPORT RESULTS", ELECTRIC_SAPPHIRE, self.fonts.orbitron, font_size=11,
        )
        self.btn_export_detail.clicked.connect(self.export_group_results)
        detail_export_row.addWidget(self.btn_export_detail, stretch=1)

        self.btn_clear_detail = ThemedButton(
            "🗑 CLEAR RESULTS", WARN_COLOR, self.fonts.orbitron, font_size=11,
        )
        self.btn_clear_detail.clicked.connect(self.clear_group_results)
        detail_export_row.addWidget(self.btn_clear_detail, stretch=1)

        content_layout.addLayout(detail_export_row)

        self.btn_set_password = ThemedButton(
            "🔒 SET PHONE SESSION PASSWORD", TRUE_AZURE, self.fonts.orbitron,
            font_size=11, padding="10px",
        )
        self.btn_set_password.clicked.connect(self.open_session_password_dialog)
        content_layout.addWidget(self.btn_set_password)

        self.btn_start_server = ThemedButton(
            "🚀 START LIVE GRADING", NEON_PINK, self.fonts.orbitron,
            font_size=16, weight=QFont.Weight.Black, fixed_height=80,
            border_width=3, letter_spacing=2, support_disabled=True,
        )
        self.btn_start_server.clicked.connect(self.start_live_grading_session)
        content_layout.addWidget(self.btn_start_server)

        return page

    def open_group_detail(self, group_name):
        self.active_group_name = group_name
        self.detail_group_title.setText(group_name.upper())
        self._refresh_readiness()
        self.sub_stack.setCurrentIndex(1)

    def open_session_password_dialog(self):
        if not self.project_manager.is_active:
            show_warning(self, self.fonts.orbitron, self.fonts.mono, "Error", "No active workspace.")
            return
        dialog = SessionPasswordDialog(self.project_manager, self.fonts.orbitron, self.fonts.mono, self)
        dialog.exec()

    def _refresh_readiness(self):
        """Surfaces the same checks start_live_grading_session() enforces,
        up front, so the admin sees blockers before pressing the button
        instead of after."""
        self.readiness_list.clear()

        if not self.project_manager.is_active:
            self.readiness_list.addItem("⚠ No active workspace.")
            self.btn_start_server.setEnabled(False)
            return

        problems = self.project_manager.get_blueprint_readiness()
        if not problems:
            self.readiness_list.addItem("✅ Exam blueprint ready — you can start live grading.")
        else:
            for problem in problems:
                self.readiness_list.addItem(f"⚠ {problem}")
        self.btn_start_server.setEnabled(not problems)