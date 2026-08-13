"""
tests/test_live_session_controller.py
----------------------------------------
Tests LiveSessionController.start()'s two branches: cv_engine fails to
load (emits session_start_failed, never touches WebSocketServer) and
cv_engine loads fine (WebSocketServer gets constructed with it and
started). WebSocketServer and CVEngineBridge are both replaced with
fakes here — this file is about LiveSessionController's own wiring
logic, not re-testing either of those (see test_websocket_server.py
and test_cv_engine_bridge.py for that).
"""
from types import SimpleNamespace

import pytest

from admin_dashboard import live_session_controller as lsc_module
from admin_dashboard.live_session_controller import LiveSessionController
from admin_dashboard.cv_engine_bridge import CVEngineError


class FakeProjectManager:
    def __init__(self, db_path):
        self.db_path = db_path

    def get_session_password(self):
        return "test-pass"


class FakeWebSocketServerThread:
    """Stands in for the real QThread-based WebSocketServer — records
    what it was constructed with and what happened to it, without
    spinning up asyncio or a real thread."""
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self._running = False
        FakeWebSocketServerThread.instances.append(self)

        class _Signal:
            def connect(self, *_): pass
        self.log_signal = _Signal()
        self.phone_connected = _Signal()
        self.phone_disconnected = _Signal()
        self.score_saved = _Signal()
        self.score_removed = _Signal()

    def start(self):
        self.started = True
        self._running = True

    def isRunning(self):
        return self._running

    def stop(self):
        self._running = False

    def wait(self):
        pass

    def get_connected_phones_snapshot(self):
        return []


@pytest.fixture(autouse=True)
def reset_fake_instances():
    FakeWebSocketServerThread.instances.clear()
    yield
    FakeWebSocketServerThread.instances.clear()


def test_start_emits_failure_signal_when_engine_fails_to_load(tmp_db_path, monkeypatch):
    def raise_error(*a, **kw):
        raise CVEngineError("no .so found")
    monkeypatch.setattr(lsc_module, "CVEngineBridge", raise_error)
    monkeypatch.setattr(lsc_module, "WebSocketServer", FakeWebSocketServerThread)

    controller = LiveSessionController(FakeProjectManager(tmp_db_path))
    failures = []
    started = []
    controller.session_start_failed.connect(lambda reason: failures.append(reason))
    controller.session_started.connect(lambda: started.append(True))

    controller.start("Section A", {"mcq_count": 5})

    assert failures == ["no .so found"]
    assert started == []
    assert not controller.is_running
    assert FakeWebSocketServerThread.instances == []  # never even attempted


def test_start_success_constructs_server_with_engine_and_starts_it(tmp_db_path, monkeypatch):
    fake_engine = SimpleNamespace(marker="fake-engine")
    monkeypatch.setattr(lsc_module, "CVEngineBridge", lambda **kw: fake_engine)
    monkeypatch.setattr(lsc_module, "WebSocketServer", FakeWebSocketServerThread)

    controller = LiveSessionController(FakeProjectManager(tmp_db_path))
    started = []
    controller.session_started.connect(lambda: started.append(True))

    controller.start("Section A", {"mcq_count": 5})

    assert started == [True]
    assert len(FakeWebSocketServerThread.instances) == 1
    server = FakeWebSocketServerThread.instances[0]
    assert server.started is True
    assert server.kwargs["cv_engine"] is fake_engine
    assert server.kwargs["group_name"] == "Section A"
    assert server.kwargs["session_password"] == "test-pass"


def test_start_twice_raises_without_touching_engine_again(tmp_db_path, monkeypatch):
    fake_engine = SimpleNamespace(marker="fake-engine")
    monkeypatch.setattr(lsc_module, "CVEngineBridge", lambda **kw: fake_engine)
    monkeypatch.setattr(lsc_module, "WebSocketServer", FakeWebSocketServerThread)

    controller = LiveSessionController(FakeProjectManager(tmp_db_path))
    controller.start("Section A", {"mcq_count": 5})

    with pytest.raises(RuntimeError, match="already live"):
        controller.start("Section A", {"mcq_count": 5})

    assert len(FakeWebSocketServerThread.instances) == 1  # second start() never got that far


def test_stop_clears_cv_engine_reference(tmp_db_path, monkeypatch):
    fake_engine = SimpleNamespace(marker="fake-engine")
    monkeypatch.setattr(lsc_module, "CVEngineBridge", lambda **kw: fake_engine)
    monkeypatch.setattr(lsc_module, "WebSocketServer", FakeWebSocketServerThread)

    controller = LiveSessionController(FakeProjectManager(tmp_db_path))
    controller.start("Section A", {"mcq_count": 5})
    assert controller.cv_engine is fake_engine

    controller.stop()
    assert controller.cv_engine is None
    assert not controller.is_running
