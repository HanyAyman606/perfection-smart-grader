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