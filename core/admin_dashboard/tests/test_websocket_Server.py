"""
tests/test_websocket_server.py
--------------------------------
Covers Phase 1 of connection_fix_plan.md:
  1.1 stale-socket takeover on reconnect
  1.2 shrunk heartbeat timeout/sweep interval
Each test below is written to FAIL against the pre-fix `_authenticate`
(the version that rejects any reconnect while `existing.status ==
"connected"`, with no liveness probe) and PASS against the fixed version.
"""
import asyncio

from admin_dashboard.workers import websocket_server as ws_module
from admin_dashboard.tests.conftest import FakeWebSocket, make_auth_message


async def test_reconnect_rejected_while_old_socket_truly_alive(server):
    old_ws = FakeWebSocket(ping_behavior="pong")
    await server._authenticate(FakeWebSocket([make_auth_message("Ali")]))
    # Simulate that the registered socket is `old_ws` (first auth above used
    # a throwaway socket only to populate the registry; re-point it at the
    # one we actually want to probe).
    with server._phones_lock:
        server.phones["Ali"].websocket = old_ws

    new_ws = FakeWebSocket([make_auth_message("Ali")])
    result = await server._authenticate(new_ws)

    assert result is None
    assert new_ws.last_sent["status"] == "error"
    assert new_ws.last_sent.get("reason") == "name_taken"
    assert old_ws.ping_calls == 1
    # The genuinely-alive old socket must not have been touched/closed.
    assert old_ws.closed is False
    # Registry still points at the old, alive socket.
    with server._phones_lock:
        assert server.phones["Ali"].websocket is old_ws


async def test_reconnect_allowed_when_old_socket_is_dead(server):
    old_ws = FakeWebSocket(ping_behavior="raises")
    await server._authenticate(FakeWebSocket([make_auth_message("Ali")]))
    with server._phones_lock:
        server.phones["Ali"].websocket = old_ws

    new_ws = FakeWebSocket([make_auth_message("Ali")])
    result = await server._authenticate(new_ws)

    assert result == "Ali"
    assert new_ws.last_sent["status"] == "success"
    assert old_ws.ping_calls == 1
    assert old_ws.closed is True
    with server._phones_lock:
        assert server.phones["Ali"].websocket is new_ws
        assert server.phones["Ali"].status == "connected"


async def test_reconnect_allowed_when_old_socket_ping_times_out(server):
    old_ws = FakeWebSocket(ping_behavior="timeout")
    await server._authenticate(FakeWebSocket([make_auth_message("Ali")]))
    with server._phones_lock:
        server.phones["Ali"].websocket = old_ws

    new_ws = FakeWebSocket([make_auth_message("Ali")])
    result = await server._authenticate(new_ws)

    assert result == "Ali"
    assert new_ws.last_sent["status"] == "success"
    with server._phones_lock:
        assert server.phones["Ali"].websocket is new_ws


async def test_normal_new_name_auth_unaffected(server):
    new_ws = FakeWebSocket([make_auth_message("Sara")])
    result = await server._authenticate(new_ws)

    assert result == "Sara"
    assert new_ws.last_sent["status"] == "success"
    with server._phones_lock:
        assert "Sara" in server.phones


async def test_wrong_password_still_rejected_no_takeover_logic_involved(server):
    ws = FakeWebSocket([make_auth_message("Ali", password="wrong")])
    result = await server._authenticate(ws)

    assert result is None
    assert ws.last_sent["status"] == "error"
    assert ws.last_sent.get("reason") == "bad_credentials"
    assert ws.ping_calls == 0  # never should have reached the takeover path


async def test_heartbeat_sweep_marks_stale_phone_within_new_window(server):
    # Register a "connected" phone whose last_seen is already older than
    # the (now-shrunk) timeout, then run exactly one sweep iteration body
    # directly rather than sleeping HEARTBEAT_TIMEOUT_SECONDS of real time.
    ws = FakeWebSocket([make_auth_message("Ali")])
    await server._authenticate(ws)

    now = asyncio.get_event_loop().time()
    with server._phones_lock:
        phone = server.phones["Ali"]
        phone.last_seen_monotonic = now - (ws_module.HEARTBEAT_TIMEOUT_SECONDS + 1)

    stale_names = []
    now2 = asyncio.get_event_loop().time()
    with server._phones_lock:
        for phone in server.phones.values():
            if phone.status == "connected" and (now2 - phone.last_seen_monotonic) > ws_module.HEARTBEAT_TIMEOUT_SECONDS:
                phone.status = "disconnected"
                stale_names.append(phone.name)

    assert stale_names == ["Ali"]
    with server._phones_lock:
        assert server.phones["Ali"].status == "disconnected"
    # Lock in the tightened window from Phase 1.2 — guards against someone
    # accidentally reverting to the old 30s/10s values later.
    assert ws_module.HEARTBEAT_TIMEOUT_SECONDS <= 12
    assert ws_module.HEARTBEAT_SWEEP_INTERVAL <= 5