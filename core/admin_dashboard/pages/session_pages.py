"""
pages/session_pages.py
------------------------
"Session Manager" page: an internal 3-screen flow —
  0. Group Hub       — grid of group cards (create/select a group)
  1. Group Detail    — import Excel roster, start live grading
  2. Live Monitoring — socket server log, stop/export

The three screens share one mutable piece of state (which group is
currently open) and one background thread — passing that through extra
constructor args to fully independent widgets would add indirection
without real decoupling benefit, so SessionManagerPage stays one QWidget
composed from three mixins by responsibility instead:

    GroupHubMixin    — pages/session/group_hub_mixin.py    (screen 0)
    GroupDetailMixin — pages/session/group_detail_mixin.py (screen 1)
    LiveMonitorMixin — pages/session/live_monitor_mixin.py (screen 2)

This file used to hold all three screens' UI-building and event-handling
code directly (518 lines, one God Object). The split above keeps every
method's behavior byte-for-byte identical — only *where the code lives*
changed — so touching e.g. the results-export flow can no longer
accidentally break the group-card grid 250 lines away in the same file.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QStackedWidget

from admin_dashboard.group_registry import group_registry
from admin_dashboard.live_session_controller import LiveSessionController
from admin_dashboard.pages.session.group_hub_mixin import GroupHubMixin
from admin_dashboard.pages.session.group_detail_mixin import GroupDetailMixin
from admin_dashboard.pages.session.live_monitor_mixin import LiveMonitorMixin


class SessionManagerPage(QWidget, GroupHubMixin, GroupDetailMixin, LiveMonitorMixin):
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