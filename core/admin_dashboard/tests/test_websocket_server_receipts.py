"""
tests/test_websocket_server_receipts.py
------------------------------------------
Covers Phase 2 of REFACTOR_PLAN.md — Finding 2 (duplicated receipt-
printing block in websocket_server.py, extracted into
WebSocketServer._print_receipt_async()).

print_ultimate_receipt does real ESC/POS I/O against a physical/virtual
printer, so it's mocked here — these tests exist to confirm BOTH call
sites (_handle_submit_score's success path, and
_handle_resolve_duplicate's "overwrite" branch) still reach the shared
helper with the right student_id/score fields/phone_name after the
dedup, not to test the printer driver itself.
"""
import asyncio
from contextlib import contextmanager
from unittest.mock import patch

from admin_dashboard.tests.conftest import FakeWebSocket


@contextmanager
def _capture_executor_future():
    """_print_receipt_async fires print_ultimate_receipt via
    loop.run_in_executor(...) without awaiting the returned future
    (fire-and-forget, intentionally — a slow printer shouldn't block the
    asyncio loop). That means a test can't just `await` the handler call
    and expect the executor's real background thread to have finished by
    the time it returns. This patches run_in_executor to capture the
    future it returns, so a test can `await` that future directly
    afterward instead of racing the background thread with an arbitrary
    sleep."""
    loop = asyncio.get_event_loop()
    real_run_in_executor = loop.run_in_executor
    captured = {}

    def spy_run_in_executor(executor, func, *args):
        future = real_run_in_executor(executor, func, *args)
        captured["future"] = future
        return future

    loop.run_in_executor = spy_run_in_executor
    try:
        yield captured
    finally:
        loop.run_in_executor = real_run_in_executor


def _submit_score_message(student_id="S001", **overrides):
    msg = {
        "type": "submit_score",
        "student_id": student_id,
        "request_id": "req-1",
        "mcq_score": 8.0,
        "essay_total": 2.0,
        "total_score": 10.0,
        "mistakes": [{"question": 1, "correct": "A", "given": "B"}],
        "answer_version": "A",
        "group_type": "M",
    }
    msg.update(overrides)
    return msg


# ----------------------------------------------------------------------
# _handle_submit_score -> _print_receipt_async
# ----------------------------------------------------------------------

async def test_submit_score_success_prints_receipt_with_submitted_fields(server):
    ws = FakeWebSocket()
    with patch(
        "admin_dashboard.workers.websocket_server.print_ultimate_receipt"
    ) as mock_print, patch(
        "admin_dashboard.workers.websocket_server.build_receipt_data"
    ) as mock_build:
        # Wraps the real implementation so the actual receipt-shaping
        # logic still runs — we're asserting on what it was CALLED with,
        # not replacing its behavior.
        from admin_dashboard.printing.receipt_printer import build_receipt_data as real_build
        mock_build.side_effect = real_build

        with _capture_executor_future() as captured:
            msg = _submit_score_message()
            await server._handle_submit_score(ws, "Ali", msg)
            await captured["future"]  # wait for the real background thread to finish

        # The save succeeded -> a success result was sent back.
        assert ws.last_sent["status"] == "success"
        assert ws.last_sent["student_id"] == "S001"

        # The shared helper was reached with the submitted score fields.
        mock_build.assert_called_once()
        _, kwargs = mock_build.call_args
        assert kwargs["student_id"] == "S001"
        assert kwargs["mcq_score"] == 8.0
        assert kwargs["essay_score"] == 2.0
        assert kwargs["total_score"] == 10.0
        assert kwargs["mistakes"] == [{"question": 1, "correct": "A", "given": "B"}]

        mock_print.assert_called_once()
        args = mock_print.call_args.args
        assert args[0] == server.printer_name
        assert args[2] == "Ali"  # phone_name


async def test_submit_score_duplicate_does_not_print_receipt(server):
    """A duplicate submission returns early before ever reaching the save
    (and therefore the receipt) — must not fire a receipt for a score
    that was never actually saved."""
    ws = FakeWebSocket()
    await server._run_db(
        server.repo.save_grade, student_id="S001", mcq_score=8.0, essay_total=2.0,
        total_score=10.0, mistakes=[], answer_version="A", group_type="M",
    )

    with patch("admin_dashboard.workers.websocket_server.print_ultimate_receipt") as mock_print:
        await server._handle_submit_score(ws, "Ali", _submit_score_message())
        assert ws.last_sent["status"] == "duplicate"
        mock_print.assert_not_called()


# ----------------------------------------------------------------------
# _handle_resolve_duplicate("overwrite") -> _print_receipt_async
# ----------------------------------------------------------------------

async def test_resolve_duplicate_overwrite_prints_receipt_with_new_payload(server):
    ws = FakeWebSocket()
    await server._run_db(
        server.repo.save_grade, student_id="S001", mcq_score=5.0, essay_total=1.0,
        total_score=6.0, mistakes=[], answer_version="A", group_type="M",
    )

    resolve_msg = {
        "type": "resolve_duplicate",
        "student_id": "S001",
        "request_id": "req-2",
        "action": "overwrite",
        "new_score_payload": {
            "mcq_score": 9.0, "essay_total": 2.0, "total_score": 11.0,
            "mistakes": [{"question": 3, "correct": "C", "given": "D"}],
            "answer_version": "A", "group_type": "M",
        },
    }

    with patch(
        "admin_dashboard.workers.websocket_server.print_ultimate_receipt"
    ) as mock_print, patch(
        "admin_dashboard.workers.websocket_server.build_receipt_data"
    ) as mock_build:
        from admin_dashboard.printing.receipt_printer import build_receipt_data as real_build
        mock_build.side_effect = real_build

        with _capture_executor_future() as captured:
            await server._handle_resolve_duplicate(ws, "Ali", resolve_msg)
            await captured["future"]

        mock_build.assert_called_once()
        _, kwargs = mock_build.call_args
        assert kwargs["student_id"] == "S001"
        assert kwargs["mcq_score"] == 9.0       # from new_score_payload, NOT the old 5.0
        assert kwargs["total_score"] == 11.0
        assert kwargs["mistakes"] == [{"question": 3, "correct": "C", "given": "D"}]

        mock_print.assert_called_once()
        assert mock_print.call_args.args[2] == "Ali"

    existing = await server._run_db(server.repo.get_existing_grade, "S001")
    assert existing["score"] == 11.0


async def test_resolve_duplicate_discard_does_not_print_receipt(server):
    """discard_both removes the grade rather than saving a new one — no
    receipt should be printed, since no new score exists to hand out."""
    ws = FakeWebSocket()
    await server._run_db(
        server.repo.save_grade, student_id="S001", mcq_score=5.0, essay_total=1.0,
        total_score=6.0, mistakes=[], answer_version="A", group_type="M",
    )
    resolve_msg = {
        "type": "resolve_duplicate", "student_id": "S001",
        "request_id": "req-3", "action": "discard_both",
    }

    with patch("admin_dashboard.workers.websocket_server.print_ultimate_receipt") as mock_print:
        await server._handle_resolve_duplicate(ws, "Ali", resolve_msg)
        mock_print.assert_not_called()

    assert await server._run_db(server.repo.get_existing_grade, "S001") is None