"""
tests/conftest.py
------------------
Shared fixtures for admin_dashboard tests. FakeWebSocket stands in for a
real `websockets` connection object so server logic can be exercised
without opening real sockets.
"""
import asyncio
import json
import tempfile
import pytest

import websockets

from admin_dashboard.workers import websocket_server as ws_module
from admin_dashboard.workers.websocket_server import WebSocketServer
from admin_dashboard.grading_repository import new_session_id


class FakeWebSocket:
    """Minimal stand-in for a `websockets` server-side connection.

    - `.send()` records every message so tests can assert on what was sent.
    - `.recv()` returns queued incoming messages, or raises ConnectionClosed
      once the queue is exhausted (matches real behavior on a dead socket).
    - `.ping()` / `.close()` behavior is controllable per-instance so tests
      can simulate a socket that's genuinely alive vs. silently dead.
    """

    def __init__(self, incoming: list[str] | None = None, *, ping_behavior: str = "pong"):
        self._incoming = list(incoming or [])
        self.sent: list[dict] = []
        self.closed = False
        # "pong": ping() resolves normally (socket alive)
        # "timeout": ping()'s waiter never resolves (socket silently dead)
        # "raises": ping() itself raises ConnectionClosed immediately
        self.ping_behavior = ping_behavior
        self.ping_calls = 0

    async def recv(self):
        if not self._incoming:
            raise websockets.exceptions.ConnectionClosed(None, None)
        return self._incoming.pop(0)

    async def send(self, message: str):
        if self.closed:
            raise websockets.exceptions.ConnectionClosed(None, None)
        self.sent.append(json.loads(message))

    async def close(self):
        self.closed = True

    async def ping(self):
        self.ping_calls += 1
        if self.ping_behavior == "raises":
            raise websockets.exceptions.ConnectionClosed(None, None)

        loop = asyncio.get_event_loop()
        waiter = loop.create_future()
        if self.ping_behavior == "pong":
            waiter.set_result(None)
        # "timeout": leave the future pending forever — caller's
        # asyncio.wait_for(..., timeout=...) is what should time it out.
        return waiter

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.recv()
        except websockets.exceptions.ConnectionClosed:
            raise StopAsyncIteration

    @property
    def last_sent(self) -> dict:
        assert self.sent, "no message was sent on this socket"
        return self.sent[-1]


def make_auth_message(name: str, password: str = "12345678") -> str:
    return json.dumps({"type": "auth", "name": name, "password": password})


@pytest.fixture
def tmp_db_path(tmp_path):
    return str(tmp_path / "roster.db")


@pytest.fixture
def server(tmp_db_path):
    """A WebSocketServer instance with no real QThread/event-loop started —
    tests drive its async methods directly via asyncio, same pattern as
    calling any other coroutine under pytest-asyncio."""
    packet_data = {
        "exam_name": "Test Exam",
        "mcq_ranges": [],
        "essay_points_map": {},
    }
    srv = WebSocketServer(
        packet_data=packet_data,
        db_path=tmp_db_path,
        session_id=new_session_id(),
        group_name="Group A",
        session_password="12345678",
    )
    return srv


@pytest.fixture(scope="session")
def qtbot_app():
    """A single shared QApplication instance for any test that
    constructs real Qt widgets (as opposed to the async server tests
    above, which never touch Qt at all). Session-scoped since Qt only
    allows one QApplication per process — creating a second one raises.
    Requires QT_QPA_PLATFORM=offscreen in the test environment (no real
    display available in CI/sandboxes)."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app