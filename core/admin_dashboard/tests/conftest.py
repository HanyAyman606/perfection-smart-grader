"""
tests/conftest.py
-------------------
Shared fixtures for the lab-side grading pipeline tests. Nothing here
touches Qt widgets or a real cv_engine binary — WebSocketServer and
LiveSessionController are both plain Python objects underneath their
QThread/QObject base classes, so they're testable without a running
QApplication as long as we never call .start()/.run() on a real thread
(the tests that need WebSocketServer's handler coroutines call them
directly as coroutines instead).
"""
import base64
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


@pytest.fixture
def tmp_db_path(tmp_path):
    return str(tmp_path / "roster.db")


@pytest.fixture
def master_packet():
    """Matches the exact shape ProjectManager.build_sync_packet() produces
    (snake_case field names) — see project_manager.py."""
    return {
        "exam_name": "Test Midterm",
        "exam_mode": "standard",
        "mcq_count": 5,
        "mcq_ranges": [{"start": 1, "end": 5, "points": 2.0}],
        "has_essays": True,
        "essay_points_map": {},
        "group_name": "Section A",
        "answer_versions": ["A"],
        "model_answers": {"A": {"1": "A", "2": "B", "3": "C", "4": "D", "5": "A"}},
        "voided_questions": {"A": [3]},
        "choices_per_question": 4,
        "mcq_columns": {"num_cols": 1, "columns": {"1": 5}},
        "id": {"num_digits": 4, "num_letters": 0, "letters": []},
    }


@pytest.fixture
def fake_image_b64():
    return base64.b64encode(b"not-a-real-jpeg-but-bytes-are-bytes-for-this-test").decode("ascii")


class FakeCVEngine:
    """Duck-types CVEngineBridge's public surface. process_exam() returns
    whatever result was queued via queue_result()/queue_error(), in
    order, one per call — lets a test script a specific sequence of
    engine outcomes (e.g. LOW confidence then a clean read) without
    touching ctypes or a real .so at all."""

    def __init__(self):
        self._queue = []
        self.calls = []

    def queue_result(self, result: dict):
        self._queue.append(("result", result))

    def queue_error(self, exc: Exception):
        self._queue.append(("error", exc))

    def queue_sleep(self, seconds: float, then_result: dict):
        self._queue.append(("sleep", seconds, then_result))

    def config_kwargs_from_master_packet(self, packet: dict) -> dict:
        id_cfg = packet.get("id", {})
        return dict(
            num_questions=packet.get("mcq_count", 0),
            num_choices=packet.get("choices_per_question", 4),
            mcq_columns=packet.get("mcq_columns", {}),
            id_digits=id_cfg.get("num_digits", 0),
            id_letters=id_cfg.get("num_letters", 0),
            id_letter_values=id_cfg.get("letters", []),
        )

    def process_exam(self, image_path: str, **kwargs) -> dict:
        import time
        self.calls.append((image_path, kwargs))
        if not self._queue:
            raise AssertionError("FakeCVEngine.process_exam called with nothing queued")
        item = self._queue.pop(0)
        if item[0] == "result":
            return item[1]
        elif item[0] == "error":
            raise item[1]
        elif item[0] == "sleep":
            time.sleep(item[1])
            return item[2]


class FakeWebSocket:
    """Captures every JSON message sent to it, in order, as parsed dicts
    — so tests can assert on message content/sequence without caring
    about the wire format."""

    def __init__(self):
        self.sent = []

    async def send(self, raw: str):
        import json
        self.sent.append(json.loads(raw))


@pytest.fixture
def fake_cv_engine():
    return FakeCVEngine()


@pytest.fixture
def fake_websocket():
    return FakeWebSocket()


def clean_questions(answers: dict) -> list:
    """Builds a process_exam()-shaped "questions" list from a simple
    {question_number: given_answer_or_None} map, all state ANSWERED
    unless the value is None (then BLANK)."""
    out = []
    for qnum, given in answers.items():
        if given is None:
            out.append({"question_number": qnum, "state": "BLANK", "answer": None})
        else:
            out.append({"question_number": qnum, "state": "ANSWERED", "answer": given})
    return out
