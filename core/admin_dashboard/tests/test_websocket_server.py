"""
tests/test_websocket_server.py
---------------------------------
Exercises WebSocketServer's handler coroutines directly (not through a
real websocket connection) against a real temp-file sqlite db via
GradingRepository — so these are true integration tests of the
grade_submission -> cv_engine -> scoring -> save -> reply pipeline,
with only the CV engine and the network socket faked out.

We never call .start()/.run() (the QThread machinery) — the handlers
are plain coroutines and are awaited directly with asyncio.run(), which
is what lets these run without a QApplication event loop.
"""
import asyncio
import base64
import json

import pytest

from admin_dashboard import workers
from admin_dashboard.workers.websocket_server import WebSocketServer
from admin_dashboard.cv_engine_bridge import CVEngineError
from admin_dashboard.grading_repository import new_session_id


def make_server(tmp_db_path, master_packet, fake_cv_engine) -> WebSocketServer:
    return WebSocketServer(
        packet_data=master_packet,
        db_path=tmp_db_path,
        session_id=new_session_id(),
        group_name="Section A",
        cv_engine=fake_cv_engine,
        session_password="test-pass",
    )


def exam_result(student_id="1234", confidence="HIGH", answers=None):
    from admin_dashboard.tests.conftest import clean_questions
    answers = answers if answers is not None else {1: "A", 2: "B", 4: "D", 5: "A"}
    return {
        "status": "OK",
        "student_id": student_id,
        "confidence": confidence,
        "questions": clean_questions(answers),
    }


# ----------------------------------------------------------------------
# grade_submission: success path
# ----------------------------------------------------------------------

def test_grade_submission_success_saves_and_replies(tmp_db_path, master_packet, fake_cv_engine,
                                                      fake_websocket, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result())
    saved = []
    server.score_saved.connect(lambda sid, score: saved.append((sid, score)))

    msg = {"type": "grade_submission", "request_id": "r1", "image_b64": fake_image_b64,
           "answer_version": "A", "essay_total": 10.0, "group_type": "morning"}

    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    assert [m["type"] for m in fake_websocket.sent] == ["grade_ack", "score_result"]
    result = fake_websocket.sent[-1]
    assert result["status"] == "success"
    assert result["request_id"] == "r1"
    assert result["student_id"] == "1234"
    assert result["total_score"] == 8.0 + 10.0  # 4 correct * 2pts + essay
    assert saved == [("1234", 18.0)]

    stored = server.repo.get_existing_grade("1234")
    assert stored["score"] == 18.0


def test_grade_submission_bumps_scan_count(tmp_db_path, master_packet, fake_cv_engine,
                                            fake_websocket, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result())
    with server._phones_lock:
        from admin_dashboard.workers.websocket_server import ConnectedPhone
        server.phones["Phone-1"] = ConnectedPhone(name="Phone-1", websocket=fake_websocket)

    msg = {"type": "grade_submission", "request_id": "r1", "image_b64": fake_image_b64,
           "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    assert server.phones["Phone-1"].scan_count == 1


# ----------------------------------------------------------------------
# grade_submission: retake / low-confidence path
# ----------------------------------------------------------------------

def test_low_confidence_triggers_retake_and_does_not_save(tmp_db_path, master_packet,
                                                            fake_cv_engine, fake_websocket, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result(confidence="LOW"))

    msg = {"type": "grade_submission", "request_id": "r2", "image_b64": fake_image_b64,
           "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    result = fake_websocket.sent[-1]
    assert result["status"] == "needs_retake"
    assert server.repo.get_existing_grade("1234") is None


def test_missing_student_id_triggers_retake(tmp_db_path, master_packet, fake_cv_engine,
                                             fake_websocket, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result(student_id=None))

    msg = {"type": "grade_submission", "request_id": "r3", "image_b64": fake_image_b64,
           "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    result = fake_websocket.sent[-1]
    assert result["status"] == "needs_retake"
    assert "NO_STUDENT_ID" in result["warnings"]


# ----------------------------------------------------------------------
# grade_submission: bad input, engine errors, timeout
# ----------------------------------------------------------------------

def test_missing_image_returns_error_without_ack(tmp_db_path, master_packet, fake_cv_engine, fake_websocket):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    msg = {"type": "grade_submission", "request_id": "r4", "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    # No ack should be sent for input that never made it past validation —
    # only the single error reply.
    assert [m["type"] for m in fake_websocket.sent] == ["score_result"]
    assert fake_websocket.sent[0]["status"] == "error"


def test_oversized_image_rejected(tmp_db_path, master_packet, fake_cv_engine, fake_websocket, monkeypatch):
    monkeypatch.setattr(workers.websocket_server, "MAX_IMAGE_BYTES", 10)
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    big_b64 = base64.b64encode(b"x" * 100).decode()
    msg = {"type": "grade_submission", "request_id": "r5", "image_b64": big_b64,
           "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    assert fake_websocket.sent[-1]["status"] == "error"


def test_engine_error_replies_with_error_status(tmp_db_path, master_packet, fake_cv_engine,
                                                 fake_websocket, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_error(CVEngineError("simulated engine crash"))

    msg = {"type": "grade_submission", "request_id": "r6", "image_b64": fake_image_b64,
           "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    result = fake_websocket.sent[-1]
    assert result["status"] == "error"
    assert "simulated engine crash" in result["message"]
    assert server.repo.get_existing_grade("1234") is None


def test_engine_timeout_replies_with_error_status(tmp_db_path, master_packet, fake_cv_engine,
                                                   fake_websocket, fake_image_b64, monkeypatch):
    monkeypatch.setattr(workers.websocket_server, "CV_TIMEOUT_SECONDS", 0.05)
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_sleep(0.3, exam_result())  # sleeps longer than the patched timeout

    msg = {"type": "grade_submission", "request_id": "r7", "image_b64": fake_image_b64,
           "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    result = fake_websocket.sent[-1]
    assert result["status"] == "error"
    assert "timed out" in result["message"]


def test_temp_scan_file_removed_after_processing(tmp_db_path, master_packet, fake_cv_engine,
                                                   fake_websocket, fake_image_b64):
    import os
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result())

    msg = {"type": "grade_submission", "request_id": "r8", "image_b64": fake_image_b64,
           "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    written_path = fake_cv_engine.calls[0][0]
    assert not os.path.exists(written_path)


# ----------------------------------------------------------------------
# duplicate detection + resolution
# ----------------------------------------------------------------------

def test_second_submission_for_same_student_flags_duplicate(tmp_db_path, master_packet, fake_cv_engine,
                                                              fake_websocket, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result(answers={1: "A", 2: "B", 4: "D", 5: "A"}))
    fake_cv_engine.queue_result(exam_result(answers={1: "A", 2: "C", 4: "D", 5: "A"}))  # different score

    msg1 = {"type": "grade_submission", "request_id": "r9", "image_b64": fake_image_b64,
            "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg1))
    assert fake_websocket.sent[-1]["status"] == "success"

    ws2 = type(fake_websocket)()
    msg2 = {"type": "grade_submission", "request_id": "r10", "image_b64": fake_image_b64,
            "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(ws2, "Phone-2", msg2))

    result = ws2.sent[-1]
    assert result["status"] == "duplicate"
    assert result["previous"]["score"] == 8.0
    assert result["incoming"]["score"] == 6.0
    assert "1234" in server._pending_duplicates


def test_resolve_duplicate_overwrite_uses_cached_result_no_reprocessing(tmp_db_path, master_packet,
                                                                         fake_cv_engine, fake_websocket,
                                                                         fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result(answers={1: "A", 2: "B", 4: "D", 5: "A"}))
    fake_cv_engine.queue_result(exam_result(answers={1: "A", 2: "C", 4: "D", 5: "A"}))

    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", {
        "type": "grade_submission", "request_id": "r11", "image_b64": fake_image_b64,
        "answer_version": "A", "essay_total": 0.0}))
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-2", {
        "type": "grade_submission", "request_id": "r12", "image_b64": fake_image_b64,
        "answer_version": "A", "essay_total": 0.0}))

    calls_before = len(fake_cv_engine.calls)

    resolve_ws = type(fake_websocket)()
    asyncio.run(server._handle_resolve_duplicate(resolve_ws, "Phone-2", {
        "type": "resolve_duplicate", "student_id": "1234", "action": "overwrite", "request_id": "r13"}))

    # No new process_exam() call — the cached payload from the duplicate
    # submission is reused directly, not recomputed.
    assert len(fake_cv_engine.calls) == calls_before
    assert resolve_ws.sent[-1]["status"] == "success"
    assert resolve_ws.sent[-1]["action_taken"] == "overwrite"
    assert server.repo.get_existing_grade("1234")["score"] == 6.0
    assert "1234" not in server._pending_duplicates


def test_resolve_duplicate_discard_both_removes_grade(tmp_db_path, master_packet, fake_cv_engine,
                                                        fake_websocket, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result())
    fake_cv_engine.queue_result(exam_result())

    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", {
        "type": "grade_submission", "request_id": "r14", "image_b64": fake_image_b64,
        "answer_version": "A", "essay_total": 0.0}))
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-2", {
        "type": "grade_submission", "request_id": "r15", "image_b64": fake_image_b64,
        "answer_version": "A", "essay_total": 0.0}))

    removed = []
    server.score_removed.connect(lambda sid: removed.append(sid))

    resolve_ws = type(fake_websocket)()
    asyncio.run(server._handle_resolve_duplicate(resolve_ws, "Phone-2", {
        "type": "resolve_duplicate", "student_id": "1234", "action": "discard_both", "request_id": "r16"}))

    assert resolve_ws.sent[-1]["status"] == "success"
    assert server.repo.get_existing_grade("1234") is None
    assert removed == ["1234"]


def test_resolve_duplicate_keep_previous_leaves_original_grade(tmp_db_path, master_packet, fake_cv_engine,
                                                                fake_websocket, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result(answers={1: "A", 2: "B", 4: "D", 5: "A"}))
    fake_cv_engine.queue_result(exam_result(answers={1: "A", 2: "C", 4: "D", 5: "A"}))

    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", {
        "type": "grade_submission", "request_id": "r17", "image_b64": fake_image_b64,
        "answer_version": "A", "essay_total": 0.0}))
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-2", {
        "type": "grade_submission", "request_id": "r18", "image_b64": fake_image_b64,
        "answer_version": "A", "essay_total": 0.0}))

    resolve_ws = type(fake_websocket)()
    asyncio.run(server._handle_resolve_duplicate(resolve_ws, "Phone-2", {
        "type": "resolve_duplicate", "student_id": "1234", "action": "keep_previous", "request_id": "r19"}))

    assert resolve_ws.sent[-1]["status"] == "success"
    assert server.repo.get_existing_grade("1234")["score"] == 8.0  # unchanged, original submission's score
    assert "1234" not in server._pending_duplicates


def test_resolve_duplicate_for_unknown_student_returns_error(tmp_db_path, master_packet, fake_cv_engine,
                                                              fake_websocket):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    asyncio.run(server._handle_resolve_duplicate(fake_websocket, "Phone-1", {
        "type": "resolve_duplicate", "student_id": "9999", "action": "overwrite", "request_id": "r20"}))

    assert fake_websocket.sent[-1]["status"] == "error"
    assert "No pending duplicate" in fake_websocket.sent[-1]["message"]


# ----------------------------------------------------------------------
# stale duplicate expiry (heartbeat sweep)
# ----------------------------------------------------------------------

def test_stale_pending_duplicate_expires_via_sweep(tmp_db_path, master_packet, fake_cv_engine, monkeypatch):
    monkeypatch.setattr(workers.websocket_server, "HEARTBEAT_SWEEP_INTERVAL", 0.01)
    monkeypatch.setattr(workers.websocket_server, "STALE_DUPLICATE_SECONDS", 0)
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)

    async def run_one_sweep_iteration():
        server._pending_duplicates["1234"] = {
            "payload": {}, "phone_name": "Phone-1", "request_id": "r21",
            "created_at": asyncio.get_event_loop().time(),
        }
        task = asyncio.ensure_future(server._heartbeat_sweep_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_one_sweep_iteration())
    assert "1234" not in server._pending_duplicates
