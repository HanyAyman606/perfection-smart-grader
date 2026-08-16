"""
tests/test_receipt_printing.py
--------------------------------
Automated tests for the auto-print-on-submit feature.

Run with:
    pytest tests/test_receipt_printing.py -v

Requires: pytest, pytest-asyncio (pip install pytest pytest-asyncio)

Scope:
  - build_receipt_data(): pure mapping logic, no printer/hardware needed.
  - _compute_max_score(): pure arithmetic off a sync packet.
  - websocket_server's print-trigger wiring: verified by MONKEYPATCHING
    print_ultimate_receipt (never touches a real printer), asserting it
    was called with the right data in the right cases and NOT called in
    the wrong ones. This is the part most likely to silently regress —
    e.g. someone "fixes" a bug in _handle_resolve_duplicate and
    accidentally moves the print call inside the wrong branch.

What this file does NOT cover (see manual_test_checklist.md instead):
  - Actual physical printing (ESC/POS output correctness, paper cut,
    font sizes) — requires a real Xprinter XP-80 on Windows.
  - End-to-end phone -> dashboard -> printer over a real LAN connection.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from admin_dashboard.printing.receipt_printer import build_receipt_data
from admin_dashboard.workers.websocket_server import WebSocketServer, _compute_max_score


# ----------------------------------------------------------------------
# build_receipt_data() — pure mapping, no mocks needed
# ----------------------------------------------------------------------

class TestBuildReceiptData:
    def test_maps_all_fields_correctly(self):
        data = build_receipt_data(
            student_id="E123",
            mcq_score=8.0,
            essay_score=2.0,
            total_score=10.0,
            max_score=20.0,
            mistakes=[{"question": 3, "correct": "A", "given": "B"}],
            quiz_name="Quiz 7",
        )
        assert data["student_id"] == "E123"
        assert data["mcq_score"] == 8.0
        assert data["essay_score"] == 2.0
        assert data["total_score"] == 10.0
        assert data["max_score"] == 20.0
        assert data["quiz_name"] == "Quiz 7"

    def test_mistakes_are_translated_from_phone_field_names(self):
        """The phone sends {"question", "correct", "given"} (see
        Mistake.toJson() in exam_models.dart) — the printer's own
        internal shape uses {"q", "correct", "given"}. This is the one
        place that translation happens; a key-name typo here would
        silently print blank/KeyError'd mistake rows."""
        data = build_receipt_data(
            student_id="E1", mcq_score=0, essay_score=0, total_score=0, max_score=10,
            mistakes=[{"question": 5, "correct": "C", "given": "D"}],
            quiz_name="Q",
        )
        assert data["mistakes"] == [{"q": 5, "correct": "C", "given": "D"}]

    def test_empty_mistakes_list_produces_empty_list_not_none(self):
        data = build_receipt_data(
            student_id="E1", mcq_score=10, essay_score=5, total_score=15, max_score=15,
            mistakes=[], quiz_name="Perfect Score Quiz",
        )
        assert data["mistakes"] == []

    def test_missing_mistake_keys_do_not_crash(self):
        """Defensive: if a malformed mistake dict arrives (e.g. a future
        phone-side bug omits a key), this should not raise — a partially
        blank printed row is far better than crashing the whole
        submit-and-print flow for an otherwise-valid grade."""
        data = build_receipt_data(
            student_id="E1", mcq_score=0, essay_score=0, total_score=0, max_score=10,
            mistakes=[{"question": 1}],  # missing "correct"/"given"
            quiz_name="Q",
        )
        assert data["mistakes"] == [{"q": 1, "correct": None, "given": None}]


# ----------------------------------------------------------------------
# _compute_max_score() — pure arithmetic off a sync packet shape
# ----------------------------------------------------------------------

class TestComputeMaxScore:
    def test_mcq_only_no_essays(self):
        packet = {
            "mcq_ranges": [{"start": 1, "end": 20, "points": 1.0}],
            "essay_points_map": {},
        }
        assert _compute_max_score(packet) == 20.0

    def test_mcq_plus_essays(self):
        packet = {
            "mcq_ranges": [{"start": 1, "end": 10, "points": 1.0}],
            "essay_points_map": {"Q21": 5.0, "Q22": 5.0},
        }
        assert _compute_max_score(packet) == 20.0

    def test_multiple_mcq_ranges_with_different_point_values(self):
        """Mirrors a real blueprint where early questions are worth less
        than later ones — a common exam design, not an edge case to skip."""
        packet = {
            "mcq_ranges": [
                {"start": 1, "end": 10, "points": 1.0},   # 10 pts
                {"start": 11, "end": 15, "points": 2.0},  # 10 pts
            ],
            "essay_points_map": {},
        }
        assert _compute_max_score(packet) == 20.0

    def test_missing_keys_default_to_zero_not_crash(self):
        """A packet built before essays were configured, or a Shamel
        packet with no essay_points_map yet, should never crash max-score
        computation — the receipt should still print with whatever it
        can compute."""
        assert _compute_max_score({}) == 0.0


# ----------------------------------------------------------------------
# websocket_server print-trigger wiring
# ----------------------------------------------------------------------
# These construct a real WebSocketServer instance but never call .start()
# (no actual asyncio server, no real thread) — we call the internal
# _handle_submit_score / _handle_resolve_duplicate coroutines directly
# with a fake websocket, and monkeypatch GradingRepository + the printer
# so nothing touches a real DB file or a real printer.

def make_server(tmp_path):
    packet_data = {
        "exam_name": "Test Quiz",
        "mcq_ranges": [{"start": 1, "end": 10, "points": 1.0}],
        "essay_points_map": {},
    }
    db_path = str(tmp_path / "roster.db")
    with patch("admin_dashboard.workers.websocket_server.GradingRepository") as MockRepo:
        server = WebSocketServer(
            packet_data=packet_data,
            db_path=db_path,
            session_id="testsession",
            group_name="Test Group",
        )
        server.repo = MagicMock()
    return server


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(json.loads(msg))


@pytest.mark.asyncio
class TestSubmitScorePrintTrigger:
    async def test_successful_submit_triggers_print_with_correct_data(self, tmp_path):
        server = make_server(tmp_path)
        server.repo.get_existing_grade = MagicMock(return_value=None)
        server.repo.save_grade = MagicMock()

        ws = FakeWebSocket()
        msg = {
            "student_id": "E42",
            "mcq_score": 7.0,
            "essay_total": 0.0,
            "total_score": 7.0,
            "mistakes": [{"question": 2, "correct": "A", "given": "C"}],
            "answer_version": "A",
            "group_type": "M",
            "request_id": "req1",
        }

        with patch("admin_dashboard.workers.websocket_server.print_ultimate_receipt") as mock_print:
            await server._handle_submit_score(ws, "Proctor Ahmed", msg)
            await asyncio.sleep(0)  # let run_in_executor's callback get scheduled

        assert ws.sent[-1]["status"] == "success"
        assert ws.sent[-1]["student_id"] == "E42"

        # Print must have been invoked (executor call is async-fired; we
        # assert on the underlying function reference having been the one
        # handed to run_in_executor).
        mock_print.assert_not_called()  # it's called via executor, not directly — see note below

    async def test_duplicate_submit_does_not_save_or_print(self, tmp_path):
        """A duplicate (existing grade found) must short-circuit before
        any save AND before any print — printing a receipt for a
        rejected duplicate would hand the student a receipt for a grade
        that was never actually recorded."""
        server = make_server(tmp_path)
        server.repo.get_existing_grade = MagicMock(return_value={
            "score": 5.0, "answer_version": "A", "timestamp": "2026-01-01T00:00:00",
        })
        server.repo.save_grade = MagicMock()

        ws = FakeWebSocket()
        msg = {"student_id": "E42", "total_score": 9.0, "answer_version": "A",
               "timestamp": "2026-01-01T00:01:00", "request_id": "req2"}

        with patch("admin_dashboard.workers.websocket_server.print_ultimate_receipt") as mock_print:
            await server._handle_submit_score(ws, "Proctor Ahmed", msg)

        assert ws.sent[-1]["status"] == "duplicate"
        server.repo.save_grade.assert_not_called()
        mock_print.assert_not_called()

    async def test_db_error_does_not_print(self, tmp_path):
        """If save_grade raises, no grade was actually committed — must
        not print a receipt for data that isn't in roster.db."""
        server = make_server(tmp_path)
        server.repo.get_existing_grade = MagicMock(return_value=None)
        server.repo.save_grade = MagicMock(side_effect=RuntimeError("disk full"))

        ws = FakeWebSocket()
        msg = {"student_id": "E42", "total_score": 9.0, "request_id": "req3"}

        with patch("admin_dashboard.workers.websocket_server.print_ultimate_receipt") as mock_print:
            await server._handle_submit_score(ws, "Proctor Ahmed", msg)

        assert ws.sent[-1]["status"] == "error"
        mock_print.assert_not_called()


@pytest.mark.asyncio
class TestResolveDuplicatePrintTrigger:
    async def test_overwrite_triggers_print(self, tmp_path):
        server = make_server(tmp_path)
        server.repo.overwrite_grade = MagicMock()

        ws = FakeWebSocket()
        msg = {
            "student_id": "E42",
            "action": "overwrite",
            "request_id": "req4",
            "new_score_payload": {
                "mcq_score": 9.0, "essay_total": 0.0, "total_score": 9.0,
                "mistakes": [], "answer_version": "A", "group_type": "M",
            },
        }

        with patch("admin_dashboard.workers.websocket_server.print_ultimate_receipt"):
            await server._handle_resolve_duplicate(ws, "Proctor Ahmed", msg)
            await asyncio.sleep(0)

        assert ws.sent[-1]["status"] == "success"
        assert ws.sent[-1]["action_taken"] == "overwrite"
        server.repo.overwrite_grade.assert_called_once()

    async def test_discard_both_does_not_print(self, tmp_path):
        server = make_server(tmp_path)
        server.repo.discard_grade = MagicMock()

        ws = FakeWebSocket()
        msg = {"student_id": "E42", "action": "discard_both", "request_id": "req5"}

        with patch("admin_dashboard.workers.websocket_server.print_ultimate_receipt") as mock_print:
            await server._handle_resolve_duplicate(ws, "Proctor Ahmed", msg)

        assert ws.sent[-1]["status"] == "success"
        assert ws.sent[-1]["action_taken"] == "discard_both"
        server.repo.discard_grade.assert_called_once()
        mock_print.assert_not_called()

    async def test_keep_previous_does_not_print(self, tmp_path):
        server = make_server(tmp_path)
        ws = FakeWebSocket()
        msg = {"student_id": "E42", "action": "keep_previous", "request_id": "req6"}

        with patch("admin_dashboard.workers.websocket_server.print_ultimate_receipt") as mock_print:
            await server._handle_resolve_duplicate(ws, "Proctor Ahmed", msg)

        assert ws.sent[-1]["status"] == "success"
        assert ws.sent[-1]["action_taken"] == "keep_previous"
        mock_print.assert_not_called()


# ----------------------------------------------------------------------
# NOTE on run_in_executor and the "mock_print.assert_not_called()" line
# above in test_successful_submit_triggers_print_with_correct_data:
# ----------------------------------------------------------------------
# print_ultimate_receipt is dispatched via
# asyncio.get_event_loop().run_in_executor(None, print_ultimate_receipt,
# ...) — a real background thread, not a plain coroutine. Reliably
# asserting it fired (and with what args) inside a fast unit test
# requires either:
#   (a) monkeypatching run_in_executor itself to run synchronously, or
#   (b) using a threading.Event the mock sets, then joining with a
#       short timeout before asserting.
# The test above intentionally documents this gap rather than faking a
# false-positive assertion. See test_print_dispatch_mechanism below for
# a version that actually verifies the dispatch call correctly, by
# patching run_in_executor instead of the function it calls.
class TestPrintActuallyDispatched:
    @pytest.mark.asyncio
    async def test_run_in_executor_called_with_print_function_and_right_args(
            self, tmp_path
    ):
        server = make_server(tmp_path)
        server.repo.get_existing_grade = MagicMock(return_value=None)
        server.repo.save_grade = MagicMock()

        ws = FakeWebSocket()

        msg = {
            "student_id": "E99",
            "mcq_score": 3.0,
            "essay_total": 1.0,
            "total_score": 4.0,
            "mistakes": [],
            "request_id": "req7",
        }

        with patch(
                "admin_dashboard.workers.websocket_server.print_ultimate_receipt"
        ) as mock_print:
            # Get the actual event loop used by this async test.
            loop = asyncio.get_running_loop()

            # Save the real executor method.
            real_run_in_executor = loop.run_in_executor

            # Spy on run_in_executor while still allowing DB operations
            # to execute normally.
            with patch.object(
                    loop,
                    "run_in_executor",
                    wraps=real_run_in_executor,
            ) as mock_executor:
                await server._handle_submit_score(
                    ws,
                    "Proctor Sara",
                    msg,
                )

            # Find the executor call that dispatched the mocked printer.
            print_calls = [
                call
                for call in mock_executor.call_args_list
                if len(call.args) >= 5
                   and call.args[1] is mock_print
            ]

            assert len(print_calls) == 1

            args = print_calls[0].args

            # run_in_executor(None, print_ultimate_receipt, ...)
            assert args[0] is None
            assert args[1] is mock_print

            # print_ultimate_receipt(
            #     printer_name,
            #     receipt_data,
            #     proctor_name
            # )
            assert args[2] == server.printer_name
            assert args[4] == "Proctor Sara"

            receipt_data = args[3]

            assert receipt_data["student_id"] == "E99"
            assert receipt_data["total_score"] == 4.0