from PySide6.QtCore import QObject, Signal

from .workers.websocket_server import WebSocketServer
from .grading_repository import new_session_id, GradingRepository


class LiveSessionController(QObject):
    log_message = Signal(str)
    phones_updated = Signal(list)     # list[dict] — see WebSocketServer.get_connected_phones_snapshot
    score_count_changed = Signal(int)
    duplicate_ids_updated = Signal(list)   # list[str] — student_ids resolved as duplicates this session
    proctor_stats_updated = Signal(list)   # list[dict] — see GradingRepository.get_group_proctor_stats
    session_started = Signal()
    session_stopped = Signal()

    def __init__(self, project_manager):
        super().__init__()
        self.project_manager = project_manager
        self.server_thread = None
        self._scores_saved_count = 0
        self._duplicate_ids: list[str] = []
        self._group_name = None

    @property
    def is_running(self) -> bool:
        return self.server_thread is not None and self.server_thread.isRunning()

    @property
    def scores_saved_count(self) -> int:
        return self._scores_saved_count

    def start(self, group_name: str, master_packet: dict, printer_name: str = "Xprinter XP-80"):
        if self.is_running:
            raise RuntimeError("A grading session is already live.")

        self._group_name = group_name

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
        self._refresh_proctor_stats()
        self.session_started.emit()

    def stop(self):
        if self.is_running:
            self.server_thread.stop()
            self.server_thread.wait()
        self.session_stopped.emit()

    def reset_score_count(self):
        self._scores_saved_count = 0
        self.score_count_changed.emit(0)
        self._refresh_proctor_stats()

    def _emit_phones_snapshot(self, _phone_name=None):
        self.phones_updated.emit(self.server_thread.get_connected_phones_snapshot())

    def _refresh_proctor_stats(self):
        if not self._group_name:
            return
        stats = GradingRepository.get_group_proctor_stats(self.project_manager.db_path, self._group_name)
        self.proctor_stats_updated.emit(stats)

    def _on_score_saved(self, _student_id, _score):
        self._scores_saved_count += 1
        self.score_count_changed.emit(self._scores_saved_count)
        self._emit_phones_snapshot()
        self._refresh_proctor_stats()

    def _on_score_removed(self, _student_id):
        self._scores_saved_count = max(0, self._scores_saved_count - 1)
        self.score_count_changed.emit(self._scores_saved_count)
        self._refresh_proctor_stats()

    def _on_duplicate_resolved(self, student_id, _action):
        if student_id not in self._duplicate_ids:
            self._duplicate_ids.append(student_id)
            self.duplicate_ids_updated.emit(list(self._duplicate_ids))
        self._refresh_proctor_stats()
