"""
pages/session_pages.py
------------------------
"Session Manager" page: an internal 3-screen flow —
  0. Group Hub       — grid of group cards (create/select a group)
  1. Group Detail    — import Excel roster, start live grading
  2. Live Monitoring — socket server log, stop/export

Kept as one class (rather than 3 fully independent ones) because the three
screens share one mutable piece of state (which group is currently open)
and one background thread — splitting them further would just mean passing
that state through extra constructor args for no real decoupling benefit.
"""



from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QListWidget, QFileDialog, QStackedWidget, QDialog
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import (
    NEON_PINK, CLOUDY_SKY, SKY_AQUA, TEXT_MUTED, BG_CARD, BG_PANEL, BG_DEEP,
    TRUE_AZURE, VIVID_ROYAL, ELECTRIC_SAPPHIRE, WARN_COLOR
)
from admin_dashboard.pages.base import build_page_shell
from admin_dashboard.widgets.common import StatCard, apply_card_shadow, ThemedButton
from admin_dashboard.screens.payload_preview_dialog import PayloadPreviewDialog
from admin_dashboard.group_registry import group_registry
from admin_dashboard.screens.new_group_dialog import NewGroupDialog
from admin_dashboard.live_session_controller import LiveSessionController
from admin_dashboard.cross_workspace_group_sync import CrossWorkspaceGroupSync
from admin_dashboard.screens.session_password_dialog import SessionPasswordDialog
from admin_dashboard.screens.dialogs import show_info, show_warning, show_error, ask_yes_no, save_file_dialog

class SessionManagerPage(QWidget):
    def __init__(self, fonts, project_manager):
        super().__init__()
        self.fonts = fonts
        self.project_manager = project_manager
        self.active_group_name = None
        self.live_session = LiveSessionController(project_manager)
        self.live_session.log_message.connect(self.log_server_message)
        self.live_session.phones_updated.connect(self._render_phones)
        self.live_session.score_count_changed.connect(self._render_score_count)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.sub_stack = QStackedWidget()
        layout.addWidget(self.sub_stack)

        self.page_group_hub = self._build_group_hub_ui()
        self.page_group_detail = self._build_group_detail_ui()
        self.page_monitoring = self._build_monitoring_ui()

        self.sub_stack.addWidget(self.page_group_hub)     # Index 0
        self.sub_stack.addWidget(self.page_group_detail)  # Index 1
        self.sub_stack.addWidget(self.page_monitoring)    # Index 2

        # Observer pattern: any workspace that adds/removes a group
        # refreshes this hub too, with no direct coupling between pages.
        group_registry.group_added.connect(lambda _name: self.refresh_group_hub())
        group_registry.group_removed.connect(lambda _name: self.refresh_group_hub())
        group_registry.group_renamed.connect(lambda _old, _new: self.refresh_group_hub())

    def reset_to_hub(self):
        """Called by the dashboard whenever this page becomes active again.
        If a live grading session is already running in the background,
        show a button to jump straight back into monitoring instead of
        silently dropping the admin back to the hub with no way back."""
        is_live = self.live_session.is_running
        self.btn_return_to_live.setVisible(is_live)

        self.sub_stack.setCurrentIndex(0)
        self.refresh_group_hub()

    # ==================================================================
    # SCREEN 0: GROUP HUB
    # ==================================================================
    def _build_group_hub_ui(self):
        page = QWidget()
        content_layout = build_page_shell(page, "Groups & Rosters Hub", NEON_PINK, self.fonts.orbitron)

        self.btn_return_to_live = ThemedButton(
            "🔴 RETURN TO LIVE SESSION", NEON_PINK, self.fonts.orbitron,
            variant="solid", font_size=12, padding="16px", hover_bg="#d81d6f",
        )
        self.btn_return_to_live.clicked.connect(lambda: self.sub_stack.setCurrentIndex(2))
        self.btn_return_to_live.setVisible(False)
        content_layout.addWidget(self.btn_return_to_live)

        btn_add_group = QPushButton("+ ADD NEW GROUP")
        btn_add_group.setFont(QFont(self.fonts.orbitron, 12, QFont.Weight.Bold))
        btn_add_group.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_group.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {NEON_PINK};
                border: 2px dashed {NEON_PINK}; border-radius: 12px; padding: 20px;
            }}
            QPushButton:hover {{ background-color: {NEON_PINK}; color: #ffffff; border-style: solid; }}
        """)
        btn_add_group.clicked.connect(self.create_new_group)
        content_layout.addWidget(btn_add_group)

        self.empty_state_label = QLabel("No groups yet — tap \"+ ADD NEW GROUP\" above to create your first one.")
        self.empty_state_label.setFont(QFont(self.fonts.mono, 11))
        self.empty_state_label.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent; border: none;")
        self.empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_label.setVisible(False)
        content_layout.addWidget(self.empty_state_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        self.groups_container = QWidget()
        self.groups_container.setStyleSheet("background: transparent;")
        self.groups_layout = QGridLayout(self.groups_container)
        self.groups_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.groups_container)

        content_layout.addWidget(scroll)
        return page

    def refresh_group_hub(self):
        """Reads the global registry and draws a card for each existing group."""
        for i in reversed(range(self.groups_layout.count())):
            widget = self.groups_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        group_names = group_registry.list_groups()
        self.empty_state_label.setVisible(not group_names)

        row, col = 0, 0
        for group_name in group_names:
            card = self._build_group_card(group_name)
            self.groups_layout.addWidget(card, row, col)
            col += 1
            if col > 2:
                col = 0
                row += 1

    def _build_group_card(self, group_name):
        card = QPushButton()
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setFixedSize(300, 90)
        card.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD}; color: {SKY_AQUA};
                border: 2px solid {TRUE_AZURE}; border-radius: 12px; text-align: left; padding: 0px;
            }}
            QPushButton:hover {{ border: 2px solid {CLOUDY_SKY}; background-color: rgba(72, 149, 239, 0.1); }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 20, 18, 20)
        card_layout.setSpacing(6)

        title_row = QHBoxLayout()
        title = QLabel(group_name.upper())
        title.setFont(QFont(self.fonts.orbitron, 14, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {SKY_AQUA}; background: transparent; border: none;")

        btn_rename = QPushButton("✎")
        btn_rename.setFixedSize(34, 34)
        btn_rename.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_rename.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; "
            f"border: none; font-weight: bold; font-size: 16px; padding: 0px; text-align: center; }} "
            f"QPushButton:hover {{ color: {CLOUDY_SKY}; }}"
        )
        btn_rename.clicked.connect(lambda checked, g=group_name: self.rename_group(g))

        btn_delete = QPushButton("✕")
        btn_delete.setFixedSize(34, 34)
        btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_delete.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; "
            f"border: none; font-weight: bold; font-size: 16px; padding: 0px; text-align: center; }} "
            f"QPushButton:hover {{ color: {WARN_COLOR}; }}"
        )
        btn_delete.clicked.connect(lambda checked, g=group_name: self.delete_group(g))

        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(btn_rename)
        title_row.addWidget(btn_delete)

        subtitle = QLabel("Tap to manage")
        subtitle.setFont(QFont(self.fonts.mono, 9))
        subtitle.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent; border: none;")

        card_layout.addLayout(title_row)
        card_layout.addWidget(subtitle)

        card.clicked.connect(lambda checked, g=group_name: self.open_group_detail(g))
        apply_card_shadow(card)
        return card

    def create_new_group(self):
        clean_name, ok = NewGroupDialog.get_group_name(self.fonts.orbitron, self.fonts.mono, parent=self)
        if not (ok and clean_name):
            return

        if not group_registry.add_group(clean_name):
            show_info(self, self.fonts.orbitron, self.fonts.mono, "Already Exists",
                      f"A group named '{clean_name}' already exists.")
        # Card appears via the group_added signal → refresh_group_hub().
        # Admin stays on the hub and opens the card themselves when ready.

    def delete_group(self, group_name):
        confirmed = ask_yes_no(self, self.fonts.orbitron, self.fonts.mono,         "Delete Group",
        f"Delete '{group_name}'?\n\n"
        "This removes it from the groups list AND permanently deletes its "
        "roster from every workspace it was imported into. This cannot be undone.")

        if not confirmed: return

        CrossWorkspaceGroupSync.purge_group_everywhere(group_name)
        group_registry.remove_group(group_name)

        if self.active_group_name == group_name:
            self.active_group_name = None
            self.sub_stack.setCurrentIndex(0)

    def rename_group(self, group_name):
        new_name, ok = NewGroupDialog.get_group_name(
            self.fonts.orbitron, self.fonts.mono, parent=self,
            title="Rename Group", initial_text=group_name,
        )
        if not (ok and new_name and new_name != group_name):
            return

        if not group_registry.rename_group(group_name, new_name):
            show_info(self, self.fonts.orbitron, self.fonts.mono, "Already Exists", f"A group named '{new_name}' already exists.")
            return

        CrossWorkspaceGroupSync.rename_group_everywhere(group_name, new_name)
        if self.active_group_name == group_name:
            self.active_group_name = new_name
            self.detail_group_title.setText(new_name.upper())

    # ==================================================================
    # SCREEN 1: GROUP DETAIL
    # ==================================================================
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

    # ==================================================================
    # SCREEN 2: LIVE MONITORING
    # ==================================================================
    def _build_monitoring_ui(self):
        page = QWidget()
        content_layout = build_page_shell(page, "Live Broadcast Monitor", NEON_PINK, self.fonts.orbitron)

        stats_row = QHBoxLayout()
        self.card_scores_saved = StatCard(
            "Scores Saved", "0", SKY_AQUA, self.fonts.orbitron, self.fonts.mono, "This session"
        )
        stats_row.addWidget(self.card_scores_saved)
        content_layout.addLayout(stats_row)

        split_row = QHBoxLayout()

        log_col = QVBoxLayout()
        log_label = QLabel("ACTIVITY LOG")
        log_label.setFont(QFont(self.fonts.orbitron, 10, QFont.Weight.Bold))
        log_label.setStyleSheet(f"color: {TEXT_MUTED}; letter-spacing: 1px;")
        self.monitor_log = QListWidget()
        self.monitor_log.setStyleSheet(
            f"QListWidget {{ background-color: {BG_DEEP}; color: {NEON_PINK}; "
            f"border: 1px solid {TRUE_AZURE}; border-radius: 8px; padding: 15px; "
            f"font-family: Consolas; font-size: 13px; }}"
        )
        log_col.addWidget(log_label)
        log_col.addWidget(self.monitor_log)

        phones_col = QVBoxLayout()
        phones_label = QLabel("CONNECTED PHONES")
        phones_label.setFont(QFont(self.fonts.orbitron, 10, QFont.Weight.Bold))
        phones_label.setStyleSheet(f"color: {TEXT_MUTED}; letter-spacing: 1px;")
        self.phones_list = QListWidget()
        self.phones_list.setFixedWidth(240)
        self.phones_list.setStyleSheet(
            f"QListWidget {{ background-color: {BG_DEEP}; color: {SKY_AQUA}; "
            f"border: 1px solid {TRUE_AZURE}; border-radius: 8px; padding: 10px; "
            f"font-family: Consolas; font-size: 12px; }}"
        )
        phones_col.addWidget(phones_label)
        phones_col.addWidget(self.phones_list)

        split_row.addLayout(log_col, stretch=1)
        split_row.addLayout(phones_col)
        content_layout.addLayout(split_row)

        btn_row = QHBoxLayout()
        btn_stop = ThemedButton(
            "🛑 STOP SERVER", WARN_COLOR, self.fonts.orbitron, variant="solid",
            font_size=12, padding="15px",
        )
        btn_stop.clicked.connect(self.stop_server_and_return)

        btn_export = ThemedButton(
            "📊 EXPORT RESULTS", ELECTRIC_SAPPHIRE, self.fonts.orbitron, variant="solid",
            font_size=12, padding="15px",
        )
        btn_export.clicked.connect(self.export_group_results)

        btn_clear = ThemedButton(
            "🗑 CLEAR RESULTS", WARN_COLOR, self.fonts.orbitron, variant="solid",
            font_size=12, padding="15px",
        )
        btn_clear.clicked.connect(self.clear_group_results)

        btn_row.addWidget(btn_stop)
        btn_row.addWidget(btn_export)
        btn_row.addWidget(btn_clear)
        content_layout.addLayout(btn_row)

        return page

    def start_live_grading_session(self):

        if self.live_session.is_running:
            show_warning(
                self, self.fonts.orbitron, self.fonts.mono, "Session Already Running",
                "A grading session is already live. Stop it from the monitoring "
                "screen before starting another one."
            )
            return

        try:
            problems = self.project_manager.get_blueprint_readiness()
            if problems:
                show_error(
                    self, self.fonts.orbitron, self.fonts.mono, "Exam Not Ready",
                    "This exam isn't ready to sync yet:\n\n" + "\n".join(f"• {p}" for p in problems)
                )
                return

            master_packet = self.project_manager.build_sync_packet(self.active_group_name)

            preview = PayloadPreviewDialog(master_packet, self.fonts.orbitron, self.fonts.mono, parent=self)
            if preview.exec() != QDialog.DialogCode.Accepted:
                return

            self.live_session.start(self.active_group_name, master_packet)

            self.monitor_log.clear()
            self.phones_list.clear()
            self.sub_stack.setCurrentIndex(2)

        except Exception as e:
            show_error(self, self.fonts.orbitron, self.fonts.mono, "Initialization Error", str(e))

    def log_server_message(self, message):
        self.monitor_log.addItem(message)
        self.monitor_log.scrollToItem(self.monitor_log.item(self.monitor_log.count() - 1))

    def _render_phones(self, phones_snapshot):
        self.phones_list.clear()
        for phone in phones_snapshot:
            status_icon = "🟢" if phone["status"] == "connected" else "⚪"
            self.phones_list.addItem(f"{status_icon} {phone['name']} — {phone['scan_count']} scanned")

    def _render_score_count(self, count):
        self.card_scores_saved.update_value(str(count))

    def stop_server_and_return(self):
        self.live_session.stop()
        self.btn_return_to_live.setVisible(False)
        self.refresh_group_hub()
        self.sub_stack.setCurrentIndex(0)

    def export_group_results(self):
        """Exports the active group to an Excel file via ProjectManager."""
        if not self.active_group_name or not self.project_manager.is_active:
            return

        save_path = save_file_dialog(self, "Save Results As", "Excel Files (*.xlsx)", f"{self.active_group_name}_Results.xlsx")
        if not save_path:
            return

        try:
            self.project_manager.export_group_to_excel(self.active_group_name, save_path)
            show_info(self, self.fonts.orbitron, self.fonts.mono, "Success",
                      f"Results successfully exported to:\n{save_path}")
        except Exception as e:
            show_error(self, self.fonts.orbitron, self.fonts.mono, "Export Error", str(e))

    def clear_group_results(self):
        """Deletes every saved grade for the active group (all sessions,
        not just the most recent one) — for when you only want the next
        export to reflect a fresh live grading session. Destructive and
        irreversible, so it's gated behind an explicit warning + confirm
        instead of living on the same button as the (safe) export."""
        if not self.active_group_name or not self.project_manager.is_active:
            return

        confirmed = ask_yes_no(
            self, self.fonts.orbitron, self.fonts.mono, "⚠ Clear All Results?",
            f"This will permanently delete ALL saved grades for "
            f"\"{self.active_group_name}\" — from every past live grading "
            f"session, not just the most recent one.\n\n"
            f"This cannot be undone. Export a backup first if you're not sure.\n\n"
            f"Continue?"
        )
        if not confirmed:
            return

        try:
            deleted = self.project_manager.clear_group_grades(self.active_group_name)
            self.live_session.reset_score_count()
            show_info(self, self.fonts.orbitron, self.fonts.mono, "Cleared",
                      f"Removed {deleted} saved grade(s) for \"{self.active_group_name}\".")
        except Exception as e:
            show_error(self, self.fonts.orbitron, self.fonts.mono, "Clear Error", str(e))