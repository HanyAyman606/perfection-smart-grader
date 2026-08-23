"""
live_session_controller.py
----------------------------
Owns the lifecycle of one live grading session's background WebSocket
server: starting it, stopping it, and turning its raw signals (phone
connect/disconnect, score saved/removed, log lines) into the two pieces
of state a UI actually needs to render — a phone-status snapshot and a
running "scores saved" count.

This used to live directly inside SessionManagerPage (a QWidget also
responsible for building three whole screens of layout). Pulling it out
means the Live Monitoring screen's widgets don't need to know anything
about WebSocketServer, QThread lifecycles, or how a score count is
derived — they just connect to this controller's signals, the same
Observer pattern group_registry already uses elsewhere in this codebase.
It also means this piece is unit-testable without instantiating any Qt
widgets at all.
"""

from PySide6.QtCore import QObject, Signal

from admin_dashboard.workers.websocket_server import WebSocketServer
from admin_dashboard.grading_repository import new_session_id, GradingRepository


class LiveSessionController(QObject):
    log_message = Signal(str)
    phones_updated = Signal(list)     # list[dict] — see WebSocketServer.get_connected_phones_snapshot
    score_count_changed = Signal(int)
    duplicate_ids_updated = Signal(list)   # list[str] — student_ids resolved as duplicates this session
    session_started = Signal()
    session_stopped = Signal()

    def __init__(self, project_manager):
        super().__init__()
        self.project_manager = project_manager
        self.server_thread: WebSocketServer | None = None
        self._scores_saved_count = 0
        self._duplicate_ids: list[str] = []

    @property
    def is_running(self) -> bool:
        return self.server_thread is not None and self.server_thread.isRunning()

    @property
    def scores_saved_count(self) -> int:
        return self._scores_saved_count

    def start(self, group_name: str, master_packet: dict, printer_name: str = "Xprinter XP-80"):
        """Raises RuntimeError if a session is already running — the
        caller is expected to check `is_running` first for a friendlier
        message, but this guards the invariant either way."""
        if self.is_running:
            raise RuntimeError("A grading session is already live.")

        self.server_thread = WebSocketServer(
            packet_data=master_packet,
            db_path=self.project_manager.db_path,
            session_id=new_session_id(),
            group_name=group_name,
            session_password=self.project_manager.get_session_password(),
            printer_name=printer_name,
        )
        self.server_thread.log_signal.connect(self.log_message.emit)
        self.server_thread.phone_connected.connect(self._emit_phones_snapshot)
        self.server_thread.phone_disconnected.connect(self._emit_phones_snapshot)
        self.server_thread.score_saved.connect(self._on_score_saved)
        self.server_thread.score_removed.connect(self._on_score_removed)
        self.server_thread.duplicate_resolved.connect(self._on_duplicate_resolved)
        self.server_thread.start()

        GradingRepository.ensure_grades_schema(self.project_manager.db_path)
        existing_grades = GradingRepository.get_group_grades(self.project_manager.db_path, group_name)
        self._scores_saved_count = len(existing_grades)
        self._duplicate_ids = []

        self.score_count_changed.emit(self._scores_saved_count)
        self.duplicate_ids_updated.emit(list(self._duplicate_ids))
        self.session_started.emit()

    def stop(self):
        if self.is_running:
            self.server_thread.stop()
            self.server_thread.wait()
        self.session_stopped.emit()

    def reset_score_count(self):
        """Lets external code (e.g. a 'Clear Results' action) resync this
        controller's counter after grades were deleted out from under it,
        without needing to know this class tracks a running total at all."""
        self._scores_saved_count = 0
        self.score_count_changed.emit(0)

    def _emit_phones_snapshot(self, _phone_name=None):
        """Connected/disconnected both just mean 'redraw from the source
        of truth' — the snapshot is cheap and always correct, so there's
        no need to hand-patch individual entries."""
        self.phones_updated.emit(self.server_thread.get_connected_phones_snapshot())

    def _on_score_saved(self, _student_id, _score):
        self._scores_saved_count += 1
        self.score_count_changed.emit(self._scores_saved_count)
        self._emit_phones_snapshot()

    def _on_score_removed(self, _student_id):
        self._scores_saved_count = max(0, self._scores_saved_count - 1)
        self.score_count_changed.emit(self._scores_saved_count)

    def _on_duplicate_resolved(self, student_id, _action):
        """A duplicate scan was resolved (kept-previous, overwritten, or
        discarded) — track it for the dashboard's "Duplicate IDs" list.
        This is purely informational bookkeeping; it never touches
        _scores_saved_count, which is adjusted separately (or not at
        all) by _on_score_saved / _on_score_removed depending on the
        action taken.

        Same student_id can trigger this more than once in one session
        (e.g. a phone scans it live, then a second phone that was
        offline reconnects and syncs another scan of the same id) — the
        list should still only show that id once, not once per
        duplicate event."""
        if student_id not in self._duplicate_ids:
            self._duplicate_ids.append(student_id)
            self.duplicate_ids_updated.emit(list(self._duplicate_ids))