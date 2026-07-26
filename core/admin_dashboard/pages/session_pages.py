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

import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QListWidget, QMessageBox, QFileDialog, QInputDialog, QStackedWidget
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import (
    NEON_PINK, CLOUDY_SKY, SKY_AQUA, TEXT_MUTED, BG_CARD, BG_PANEL, BG_DEEP,
    TRUE_AZURE, VIVID_ROYAL, ELECTRIC_SAPPHIRE, WARN_COLOR
)
from admin_dashboard.pages.base import build_page_shell
from admin_dashboard.widgets.common import StatCard, apply_card_shadow
from admin_dashboard.workers.server_worker import ServerWorker


class SessionManagerPage(QWidget):
    def __init__(self, fonts, project_manager):
        super().__init__()
        self.fonts = fonts
        self.project_manager = project_manager
        self.active_group_name = None
        self.server_thread = None

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

    def reset_to_hub(self):
        """Called by the dashboard whenever this page becomes active again."""
        self.sub_stack.setCurrentIndex(0)
        self.refresh_group_hub()

    # ==================================================================
    # SCREEN 0: GROUP HUB
    # ==================================================================
    def _build_group_hub_ui(self):
        page = QWidget()
        content_layout = build_page_shell(page, "Groups & Rosters Hub", NEON_PINK, self.fonts.orbitron)

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
        """Reads the DB (via ProjectManager) and draws a card for each existing group."""
        for i in reversed(range(self.groups_layout.count())):
            widget = self.groups_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        if not self.project_manager.is_active:
            return

        try:
            groups = self.project_manager.get_groups()
        except Exception as e:
            print(f"Error loading groups: {e}")
            return

        row, col = 0, 0
        for group_name, count in groups:
            card = self._build_group_card(group_name, count)
            self.groups_layout.addWidget(card, row, col)
            col += 1
            if col > 2:
                col = 0
                row += 1

    def _build_group_card(self, group_name, count):
        card = QPushButton()
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setFixedSize(300, 120)
        card.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD}; color: {SKY_AQUA};
                border: 2px solid {TRUE_AZURE}; border-radius: 12px; text-align: left; padding: 15px;
            }}
            QPushButton:hover {{ border: 2px solid {CLOUDY_SKY}; background-color: rgba(72, 149, 239, 0.1); }}
        """)

        card_layout = QVBoxLayout(card)
        title = QLabel(group_name.upper())
        title.setFont(QFont(self.fonts.orbitron, 14, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {SKY_AQUA}; background: transparent; border: none;")

        sub = QLabel(f"{count} Students Enrolled")
        sub.setFont(QFont(self.fonts.mono, 10))
        sub.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent; border: none;")

        card_layout.addWidget(title)
        card_layout.addWidget(sub)

        card.clicked.connect(lambda checked, g=group_name: self.open_group_detail(g))
        apply_card_shadow(card)
        return card

    def create_new_group(self):
        if not self.project_manager.is_active:
            QMessageBox.warning(self, "Error", "No active workspace. Create/Open a project first.")
            return

        group_name, ok = QInputDialog.getText(self, "New Group", "Enter Group Identifier (e.g., Sidi Bishr 10 AM):")
        if ok and group_name.strip():
            self.open_group_detail(group_name.strip())

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

        roster_row = QHBoxLayout()
        btn_load_excel = QPushButton("📂 IMPORT EXCEL ROSTER(S)")
        btn_load_excel.setFont(QFont(self.fonts.orbitron, 10, QFont.Weight.Bold))
        btn_load_excel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_load_excel.setStyleSheet(
            f"QPushButton {{ background-color: {BG_PANEL}; color: {CLOUDY_SKY}; "
            f"border: 2px solid {CLOUDY_SKY}; border-radius: 8px; padding: 15px; }} "
            f"QPushButton:hover {{ background-color: {CLOUDY_SKY}; color: #000000; }}"
        )
        btn_load_excel.clicked.connect(self.load_group_excel)

        self.loaded_files_list = QListWidget()
        self.loaded_files_list.setFixedHeight(100)
        self.loaded_files_list.setStyleSheet(
            f"QListWidget {{ background-color: {BG_DEEP}; color: {TEXT_MUTED}; "
            f"border: 1px solid {VIVID_ROYAL}; border-radius: 8px; padding: 5px; }}"
        )

        roster_row.addWidget(btn_load_excel)
        roster_row.addWidget(self.loaded_files_list)
        content_layout.addLayout(roster_row)

        self.card_session_students = StatCard(
            "Group Roster", "0", SKY_AQUA, self.fonts.orbitron, self.fonts.mono, "Total loaded for this group"
        )
        content_layout.addWidget(self.card_session_students)

        self.btn_start_server = QPushButton("🚀 START LIVE GRADING")
        self.btn_start_server.setFont(QFont(self.fonts.orbitron, 16, QFont.Weight.Black))
        self.btn_start_server.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start_server.setFixedHeight(80)
        self.btn_start_server.setStyleSheet(
            f"QPushButton {{ background-color: {BG_PANEL}; color: {NEON_PINK}; "
            f"border: 3px solid {NEON_PINK}; border-radius: 12px; letter-spacing: 2px; }} "
            f"QPushButton:hover {{ background-color: {NEON_PINK}; color: #ffffff; }}"
        )
        self.btn_start_server.clicked.connect(self.start_live_grading_session)
        content_layout.addWidget(self.btn_start_server)

        return page

    def open_group_detail(self, group_name):
        self.active_group_name = group_name
        self.detail_group_title.setText(group_name.upper())
        self.loaded_files_list.clear()

        count = self.project_manager.get_group_student_count(group_name)
        self.card_session_students.update_value(str(count))
        self.sub_stack.setCurrentIndex(1)

    def load_group_excel(self):
        options = QFileDialog.Option.DontUseNativeDialog
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Group Roster Excel", "", "Excel Files (*.xlsx *.xls)", options=options
        )
        if not file_path:
            return

        try:
            total_inserted, total_in_group = self.project_manager.import_excel_roster(
                file_path, self.active_group_name
            )
            self.card_session_students.update_value(str(total_in_group))
            self.loaded_files_list.addItem(f"✔ {os.path.basename(file_path)} (+{total_inserted} records)")
        except Exception as e:
            QMessageBox.critical(self, "Data Import Error", str(e))

    # ==================================================================
    # SCREEN 2: LIVE MONITORING
    # ==================================================================
    def _build_monitoring_ui(self):
        page = QWidget()
        content_layout = build_page_shell(page, "Live Broadcast Monitor", NEON_PINK, self.fonts.orbitron)

        self.monitor_log = QListWidget()
        self.monitor_log.setStyleSheet(
            f"QListWidget {{ background-color: {BG_DEEP}; color: {NEON_PINK}; "
            f"border: 1px solid {TRUE_AZURE}; border-radius: 8px; padding: 15px; "
            f"font-family: Consolas; font-size: 13px; }}"
        )
        content_layout.addWidget(self.monitor_log)

        btn_row = QHBoxLayout()
        btn_stop = QPushButton("🛑 STOP SERVER")
        btn_stop.setFont(QFont(self.fonts.orbitron, 12, QFont.Weight.Bold))
        btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_stop.setStyleSheet(
            f"QPushButton {{ background-color: {WARN_COLOR}; color: #ffffff; border-radius: 8px; padding: 15px; }}"
        )
        btn_stop.clicked.connect(self.stop_server_and_return)

        btn_export = QPushButton("📊 EXPORT RESULTS")
        btn_export.setFont(QFont(self.fonts.orbitron, 12, QFont.Weight.Bold))
        btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_export.setStyleSheet(
            f"QPushButton {{ background-color: {ELECTRIC_SAPPHIRE}; color: #ffffff; border-radius: 8px; padding: 15px; }}"
        )
        btn_export.clicked.connect(self.export_group_results)

        btn_row.addWidget(btn_stop)
        btn_row.addWidget(btn_export)
        content_layout.addLayout(btn_row)

        return page

    def start_live_grading_session(self):
        try:
            if not os.path.exists(self.project_manager.config_path):
                QMessageBox.critical(self, "Error", "No Exam Blueprint found.")
                return

            roster = self.project_manager.get_roster(self.active_group_name)
            if not roster:
                QMessageBox.critical(
                    self, "Error", f"No roster found for '{self.active_group_name}'! Import Excel first."
                )
                return

            master_packet = self.project_manager.build_sync_packet(self.active_group_name)

            self.server_thread = ServerWorker(master_packet)
            self.server_thread.log_signal.connect(self.log_server_message)
            self.server_thread.start()

            self.monitor_log.clear()
            self.sub_stack.setCurrentIndex(2)

        except Exception as e:
            QMessageBox.critical(self, "Initialization Error", str(e))

    def log_server_message(self, message):
        self.monitor_log.addItem(message)
        self.monitor_log.scrollToItem(self.monitor_log.item(self.monitor_log.count() - 1))

    def stop_server_and_return(self):
        """Kills the socket thread and goes back to the Group Hub."""
        if self.server_thread is not None and self.server_thread.isRunning():
            self.server_thread.terminate()  # forcefully stops the loop
            self.server_thread.wait()
            self.log_server_message("SERVER SHUTDOWN.")

        self.refresh_group_hub()
        self.sub_stack.setCurrentIndex(0)

    def export_group_results(self):
        """Exports the active group to an Excel file via ProjectManager."""
        if not self.active_group_name or not self.project_manager.is_active:
            return

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Results As", f"{self.active_group_name}_Results.xlsx", "Excel Files (*.xlsx)"
        )
        if not save_path:
            return

        try:
            self.project_manager.export_group_to_excel(self.active_group_name, save_path)
            QMessageBox.information(self, "Success", f"Results successfully exported to:\n{save_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))
