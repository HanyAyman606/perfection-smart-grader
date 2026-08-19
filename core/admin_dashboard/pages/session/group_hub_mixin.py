"""
pages/session/group_hub_mixin.py
----------------------------------
Screen 0 of SessionManagerPage: the grid of group cards (create / rename /
delete / open a group). Split out of session_pages.py, which had grown into
a single 518-line class doing four unrelated jobs (hub UI, group detail UI,
live-session monitoring, and results export) — see session_pages.py's
docstring for the full rationale.

This is a MIXIN, not a standalone widget: it expects to be combined with
GroupDetailMixin and LiveMonitorMixin onto a QWidget subclass (see
SessionManagerPage) that provides self.fonts, self.groups_layout, etc. It
still touches the same shared instance state (active_group_name, sub_stack)
those other mixins use — that coupling is inherent to this being one
logical screen flow with one piece of mutable "which group is open" state,
not something a file split alone can remove. What this split buys you is
readability and independent editability: touching card-grid rendering can
no longer accidentally break the live-monitor screen 250 lines away.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QScrollArea
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import (
    NEON_PINK, CLOUDY_SKY, SKY_AQUA, TEXT_MUTED, BG_CARD, BG_PANEL, TRUE_AZURE, WARN_COLOR,
)
from admin_dashboard.pages.base import build_page_shell
from admin_dashboard.widgets.common import apply_card_shadow, ThemedButton
from admin_dashboard.group_registry import group_registry
from admin_dashboard.screens.new_group_dialog import NewGroupDialog
from admin_dashboard.cross_workspace_group_sync import CrossWorkspaceGroupSync
from admin_dashboard.screens.dialogs import show_info, ask_yes_no


class GroupHubMixin:
    """Screen 0: the group card grid. Requires self.fonts, self.sub_stack,
    self.active_group_name, self.detail_group_title (set by GroupDetailMixin)."""

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