"""
tests/test_live_session_consistency_fixes.py
------------------------------------------------
Covers three bugs reported from a live grading session:

1. Overwriting a duplicate must NOT bump the phone's scan_count or the
   session's scores-saved total a second time (overwrite_grade replaces
   the row in place — it isn't a new scan).
2. Resolving a duplicate (any action) must emit duplicate_resolved so
   the dashboard can track which student_ids were duplicates.
3. A stale socket being replaced during reconnect must not be able to
   stomp the phone back to "disconnected" after the new connection has
   already taken over (see WebSocketServer._mark_disconnected).
"""
import asyncio

from admin_dashboard.tests.conftest import FakeWebSocket, make_auth_message


async def test_overwrite_does_not_bump_scan_count_or_score_total(server):
    ws = FakeWebSocket()
    await server._run_db(
        server.repo.save_grade, student_id="S001", mcq_score=5.0, essay_total=1.0,
        total_score=6.0, mistakes=[], answer_version="A", group_type="M",
    )

    with server._phones_lock:
        from admin_dashboard.workers.websocket_server import ConnectedPhone
        server.phones["Ali"] = ConnectedPhone(name="Ali", websocket=ws, scan_count=3)

    saved_events = []
    server.score_saved.connect(lambda sid, score: saved_events.append((sid, score)))

    resolve_msg = {
        "type": "resolve_duplicate",
        "student_id": "S001",
        "request_id": "req-1",
        "action": "overwrite",
        "new_score_payload": {
            "mcq_score": 9.0, "essay_total": 2.0, "total_score": 11.0,
            "mistakes": [], "answer_version": "A", "group_type": "M",
        },
    }

    from unittest.mock import patch
    with patch("admin_dashboard.workers.websocket_server.print_ultimate_receipt"):
        await server._handle_resolve_duplicate(ws, "Ali", resolve_msg)

    # scan_count for the phone must be unchanged — overwrite isn't a new scan.
    assert server.phones["Ali"].scan_count == 3
    # score_saved must not have fired — the controller's running total
    # (and therefore the dashboard's "Scores Saved" card) must not move.
    assert saved_events == []


async def test_resolve_duplicate_emits_duplicate_resolved_for_every_action():
    from admin_dashboard.workers.websocket_server import WebSocketServer
    from admin_dashboard.grading_repository import new_session_id
    import tempfile, os

    for action, payload_extra in [
        ("keep_previous", {}),
        ("discard_both", {}),
        ("overwrite", {"new_score_payload": {
            "mcq_score": 1.0, "essay_total": 0.0, "total_score": 1.0,
            "mistakes": [], "answer_version": "A", "group_type": "M",
        }}),
    ]:
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        srv = WebSocketServer(
            packet_data={"exam_name": "X", "mcq_ranges": [], "essay_points_map": {}},
            db_path=db_path, session_id=new_session_id(), group_name="Group A",
            session_password="12345678",
        )
        await srv._run_db(
            srv.repo.save_grade, student_id="C102", mcq_score=1.0, essay_total=0.0,
            total_score=1.0, mistakes=[], answer_version="A", group_type="M",
        )

        events = []
        srv.duplicate_resolved.connect(lambda sid, act: events.append((sid, act)))

        ws = FakeWebSocket()
        from unittest.mock import patch
        with patch("admin_dashboard.workers.websocket_server.print_ultimate_receipt"):
            await srv._handle_resolve_duplicate(ws, "Ali", {
                "type": "resolve_duplicate", "student_id": "C102",
                "request_id": "r", "action": action, **payload_extra,
            })

        assert events == [("C102", action)], f"failed for action={action}"


def test_duplicate_ids_list_does_not_repeat_same_student_id():
    """Same student_id can be flagged as a duplicate more than once in a
    session (e.g. scanned live, then a phone that was offline reconnects
    and syncs another scan of the same id) — the dashboard's Duplicate
    IDs list must still only show it once."""
    from unittest.mock import MagicMock
    from admin_dashboard.live_session_controller import LiveSessionController

    controller = LiveSessionController(project_manager=MagicMock())
    controller._duplicate_ids = []

    seen_updates = []
    controller.duplicate_ids_updated.connect(lambda ids: seen_updates.append(ids))

    controller._on_duplicate_resolved("C102", "keep_previous")
    controller._on_duplicate_resolved("C102", "overwrite")  # same id again, offline sync
    controller._on_duplicate_resolved("E179", "discard_both")

    assert controller._duplicate_ids == ["C102", "E179"]
    # Only two updates should have fired — the repeat of C102 must not
    # have emitted a redundant/duplicated update.
    assert len(seen_updates) == 2


def test_proctor_stats_refresh_emits_on_score_events():
    """LiveSessionController._refresh_proctor_stats() must query
    GradingRepository.get_group_proctor_stats() and forward the result
    via proctor_stats_updated whenever a score is saved, removed, or a
    duplicate is resolved — this is what feeds the live monitor's
    'SCANS BY PROCTOR' panel. Uses a mocked repository call (unit-level,
    no real DB) since the DB-level correctness of the query itself is
    already covered by test_get_group_proctor_stats in
    test_grading_repository.py."""
    from unittest.mock import MagicMock, patch
    from admin_dashboard.live_session_controller import LiveSessionController

    project_manager = MagicMock()
    project_manager.db_path = "/fake/roster.db"
    controller = LiveSessionController(project_manager=project_manager)
    controller._group_name = "GroupA"
    controller.server_thread = MagicMock()
    controller.server_thread.get_connected_phones_snapshot.return_value = []

    seen_updates = []
    controller.proctor_stats_updated.connect(lambda stats: seen_updates.append(stats))

    fake_stats = [{"name": "Ali", "scan_count": 3}, {"name": "Mona", "scan_count": 1}]
    with patch(
        "admin_dashboard.live_session_controller.GradingRepository.get_group_proctor_stats",
        return_value=fake_stats,
    ) as mocked_query:
        controller._on_score_saved("S001", 10.0)
        controller._on_score_removed("S002")
        controller._on_duplicate_resolved("S003", "overwrite")

    # Queried once per event, always scoped to the active group.
    assert mocked_query.call_count == 3
    for call in mocked_query.call_args_list:
        assert call.args == (project_manager.db_path, "GroupA")

    # Every event's refresh reached the UI-facing signal with the data.
    assert seen_updates == [fake_stats, fake_stats, fake_stats]


def test_proctor_stats_refresh_is_a_noop_before_a_group_is_active():
    """_refresh_proctor_stats() must not query the DB (or emit anything)
    if called before start() has set _group_name — e.g. a stray
    score_saved signal firing during teardown/reset."""
    from unittest.mock import MagicMock, patch
    from admin_dashboard.live_session_controller import LiveSessionController

    controller = LiveSessionController(project_manager=MagicMock())
    assert controller._group_name is None

    seen_updates = []
    controller.proctor_stats_updated.connect(lambda stats: seen_updates.append(stats))

    with patch(
        "admin_dashboard.live_session_controller.GradingRepository.get_group_proctor_stats"
    ) as mocked_query:
        controller._refresh_proctor_stats()

    mocked_query.assert_not_called()
    assert seen_updates == []


async def test_stale_socket_replacement_does_not_leave_phone_disconnected(server):
    """Reproduces the race: an old (stale) socket is being replaced by a
    reconnect. The old connection's own read-loop notices the closure and
    calls _mark_disconnected — but only AFTER the new connection has
    already registered itself as connected. The phone must end up
    connected, not stuck disconnected."""
    old_ws = FakeWebSocket(ping_behavior="timeout")  # looks alive but won't pong -> probed as dead
    new_ws = FakeWebSocket()

    # First connection registers "Ali" on old_ws.
    from admin_dashboard.workers.websocket_server import ConnectedPhone
    with server._phones_lock:
        server.phones["Ali"] = ConnectedPhone(name="Ali", websocket=old_ws, status="connected")

    # New connection authenticates, detects old_ws is stale, replaces it.
    new_ws._incoming = [make_auth_message("Ali")]
    await server._authenticate(new_ws)

    assert server.phones["Ali"].websocket is new_ws
    assert server.phones["Ali"].status == "connected"

    # Now simulate the OLD connection's finally-block firing late, i.e.
    # after the reconnect already completed above — it must be a no-op
    # since old_ws is no longer the registered socket for "Ali".
    server._mark_disconnected("Ali", old_ws)

    assert server.phones["Ali"].status == "connected"
    assert server.phones["Ali"].websocket is new_ws

    # And a real disconnect of the CURRENT socket still works normally.
    server._mark_disconnected("Ali", new_ws)
    assert server.phones["Ali"].status == "disconnected"

def test_proctor_stats_panel_renders_from_signal(qtbot_app):
    """UI-level check for the 'SCANS BY PROCTOR' panel added to the live
    monitor screen: _render_proctor_stats() must populate
    proctor_stats_list from exactly the payload shape
    GradingRepository.get_group_proctor_stats() returns
    ([{'name': str, 'scan_count': int}, ...]), in the order given (the
    query already sorts highest-first, so this method must not re-sort
    or drop entries)."""
    from admin_dashboard.pages.session.live_monitor_mixin import LiveMonitorMixin

    class _Fonts:
        orbitron = "Arial"
        mono = "Consolas"

    class _Host(LiveMonitorMixin):
        fonts = _Fonts()

        def stop_server_and_return(self):
            pass

        def export_group_results(self):
            pass

        def clear_group_results(self):
            pass

    host = _Host()
    host._page = host._build_monitoring_ui()  # keep the page alive so its
    # child widgets (proctor_stats_list etc.) aren't GC'd — QWidget owns
    # its children only via Qt's C++ parent-child tree, not Python refs.

    host._render_proctor_stats([
        {"name": "Ali", "scan_count": 27},
        {"name": "Mona", "scan_count": 19},
        {"name": "Unknown", "scan_count": 3},
    ])

    items = [host.proctor_stats_list.item(i).text() for i in range(host.proctor_stats_list.count())]
    assert items == ["Ali — 27", "Mona — 19", "Unknown — 3"]


def test_proctor_stats_panel_clear_happens_before_session_start():
    """Real bug found via manual testing: start_live_grading_session()
    used to call self.live_session.start(...) THEN clear the proctor
    stats list — but start() synchronously emits proctor_stats_updated
    with real historical data (via GradingRepository.get_group_proctor_
    stats, not just zeros), which _render_proctor_stats() immediately
    renders. Clearing the list right after start() wiped that data back
    out, so reopening a group that had prior scans showed an empty
    'SCANS BY PROCTOR' panel instead of the carried-over history.

    Asserts against the actual source (rather than re-implementing the
    method) so this fails honestly if the ordering regresses again."""
    import inspect
    from admin_dashboard.pages.session.live_monitor_mixin import LiveMonitorMixin

    source = inspect.getsource(LiveMonitorMixin.start_live_grading_session)
    clear_pos = source.index("self.proctor_stats_list.clear()")
    start_pos = source.index("self.live_session.start(")
    assert clear_pos < start_pos, (
        "proctor_stats_list.clear() must run BEFORE live_session.start() — "
        "clearing after start() wipes out the historical proctor stats "
        "start() just emitted."
    )


def test_proctor_stats_survive_reopening_a_group_end_to_end(qtbot_app):
    """End-to-end reproduction of the reported bug: scan some papers,
    close the live grading session, open a new one for the same group —
    the 'SCANS BY PROCTOR' panel must still show the prior scans, not
    come up empty. Exercises the real start_live_grading_session flow
    (with the parts that need a live server/dialogs mocked out) rather
    than only checking source ordering, so this catches the bug even if
    a future refactor reintroduces it through a different code path."""
    from unittest.mock import MagicMock, patch
    from admin_dashboard.pages.session.live_monitor_mixin import LiveMonitorMixin
    from admin_dashboard.live_session_controller import LiveSessionController

    class _Fonts:
        orbitron = "Arial"
        mono = "Consolas"

    class _SubStack:
        def setCurrentIndex(self, i):
            pass

    project_manager = MagicMock()
    project_manager.db_path = "/fake/roster.db"
    project_manager.get_blueprint_readiness.return_value = []
    project_manager.build_sync_packet.return_value = {"exam_name": "X"}
    project_manager.get_session_password.return_value = "12345678"

    class _Host(LiveMonitorMixin):
        fonts = _Fonts()
        sub_stack = _SubStack()
        active_group_name = "GroupA"

        def export_group_results(self):
            pass

        def clear_group_results(self):
            pass

    host = _Host()
    host.project_manager = project_manager
    host._page = host._build_monitoring_ui()
    host.live_session = LiveSessionController(project_manager=project_manager)

    # Wire up the same signal connections SessionManagerPage.__init__
    # makes in session_pages.py — the mixin only defines the render
    # methods, it doesn't connect them itself.
    host.live_session.log_message.connect(host.log_server_message)
    host.live_session.phones_updated.connect(host._render_phones)
    host.live_session.score_count_changed.connect(host._render_score_count)
    host.live_session.duplicate_ids_updated.connect(host._render_duplicates)
    host.live_session.proctor_stats_updated.connect(host._render_proctor_stats)

    # Prior session already put 3 scans on the board for "Ali".
    prior_stats = [{"name": "Ali", "scan_count": 3}]

    with patch(
        "admin_dashboard.pages.session.live_monitor_mixin.PayloadPreviewDialog"
    ) as mock_preview, patch(
        "admin_dashboard.live_session_controller.WebSocketServer"
    ), patch(
        "admin_dashboard.live_session_controller.GradingRepository.ensure_grades_schema"
    ), patch(
        "admin_dashboard.live_session_controller.GradingRepository.get_group_grades",
        return_value=[],
    ), patch(
        "admin_dashboard.live_session_controller.GradingRepository.get_group_proctor_stats",
        return_value=prior_stats,
    ):
        from PySide6.QtWidgets import QDialog
        mock_preview.return_value.exec.return_value = QDialog.DialogCode.Accepted

        host.start_live_grading_session()

    items = [host.proctor_stats_list.item(i).text() for i in range(host.proctor_stats_list.count())]
    assert items == ["Ali — 3"], (
        "Reopening a group with prior scans must show its proctor history "
        "immediately, not an empty panel."
    )