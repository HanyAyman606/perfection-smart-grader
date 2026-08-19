"""
pages/session/live_monitor_mixin.py
--------------------------------------
Screen 2 of SessionManagerPage: the live-grading WebSocket monitor
(activity log, connected-phones list, stop/export/clear). Also owns
export_group_results/clear_group_results since those buttons appear on
both this screen and the group-detail screen. See group_hub_mixin.py's
docstring for why this file exists.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QDialog
from PySide6.QtGui import QFont

from admin_dashboard.theme import (
    NEON_PINK, SKY_AQUA, TEXT_MUTED, BG_DEEP, TRUE_AZURE, ELECTRIC_SAPPHIRE, WARN_COLOR,
)
from admin_dashboard.pages.base import build_page_shell
from admin_dashboard.widgets.common import StatCard, ThemedButton
from admin_dashboard.screens.payload_preview_dialog import PayloadPreviewDialog
from admin_dashboard.screens.dialogs import show_info, show_warning, show_error, ask_yes_no, save_file_dialog


class LiveMonitorMixin:
    """Screen 2: live session monitor + shared export/clear actions.
    Requires self.fonts, self.sub_stack, self.project_manager,
    self.active_group_name, self.live_session, self.btn_return_to_live
    (from GroupHubMixin)."""

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