"""
tests/test_websocket_server.py
---------------------------------
Exercises WebSocketServer's handler coroutines directly (not through a
real websocket connection) against a real temp-file sqlite db via
GradingRepository — so these are true integration tests of the
two-phase pipeline: grade_submission (CV/ONNX + scoring, cached, NOT
saved) -> confirm_submission (proctor-reviewed student_id/group_type,
THIS saves) -> score_result, with only the CV engine and the network
socket faked out.

We never call .start()/.run() (the QThread machinery) — the handlers
are plain coroutines and are awaited directly with asyncio.run(), which
is what lets these run without a QApplication event loop.
"""
import asyncio
import base64

from admin_dashboard import workers
from admin_dashboard.workers.websocket_server import WebSocketServer
from admin_dashboard.cv_engine_bridge import CVEngineError
from admin_dashboard.grading_repository import new_session_id
from admin_dashboard.tests.conftest import FakeWebSocket


def make_server(tmp_db_path, master_packet, fake_cv_engine) -> WebSocketServer:
    return WebSocketServer(
        packet_data=master_packet,
        db_path=tmp_db_path,
        session_id=new_session_id(),
        group_name="Section A",
        cv_engine=fake_cv_engine,
        session_password="test-pass",
    )


def exam_result(student_id="1234", confidence="OK", answers=None):
    from admin_dashboard.tests.conftest import clean_questions
    answers = answers if answers is not None else {1: "A", 2: "B", 4: "D", 5: "A"}
    return {
        "status": "OK",
        "student_id": student_id,
        "confidence": confidence,
        "questions": clean_questions(answers),
    }


async def grade_then_confirm(server, ws_grade, ws_confirm, phone_name, *, grade_request_id,
                              confirm_request_id, student_id, group_type=None, essay_total=0.0,
                              answer_version="A", image_b64=None):
    """Runs both phases back to back and returns (graded_reply, final_reply)."""
    grade_msg = {"type": "grade_submission", "request_id": grade_request_id, "image_b64": image_b64,
                 "answer_version": answer_version, "essay_total": essay_total}
    await server._handle_grade_submission(ws_grade, phone_name, grade_msg)
    graded_reply = ws_grade.sent[-1]

    confirm_msg = {"type": "confirm_submission", "request_id": confirm_request_id,
                    "original_request_id": grade_request_id, "student_id": student_id,
                    "group_type": group_type}
    await server._handle_confirm_submission(ws_confirm, phone_name, confirm_msg)
    final_reply = ws_confirm.sent[-1]
    return graded_reply, final_reply


# ----------------------------------------------------------------------
# Phase 1 (grade_submission): compute + cache, never saves
# ----------------------------------------------------------------------

def test_grade_submission_replies_graded_and_does_not_save(tmp_db_path, master_packet, fake_cv_engine,
                                                             fake_websocket, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result())

    msg = {"type": "grade_submission", "request_id": "r1", "image_b64": fake_image_b64,
           "answer_version": "A", "essay_total": 10.0, "group_type": "morning"}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    assert [m["type"] for m in fake_websocket.sent] == ["grade_ack", "score_result"]
    result = fake_websocket.sent[-1]
    assert result["status"] == "graded"
    assert result["request_id"] == "r1"
    assert result["student_id"] == "1234"
    assert result["mcq_score"] == 8.0
    assert result["total_score"] == 18.0

    assert server.repo.get_existing_grade("1234") is None
    assert "r1" in server._pending_submissions


def test_low_confidence_triggers_retake_and_caches_nothing(tmp_db_path, master_packet, fake_cv_engine,
                                                             fake_websocket, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result(confidence="LOW"))

    msg = {"type": "grade_submission", "request_id": "r2", "image_b64": fake_image_b64,
           "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    result = fake_websocket.sent[-1]
    assert result["status"] == "needs_retake"
    assert "r2" not in server._pending_submissions


def test_missing_image_returns_error_without_ack_or_cache(tmp_db_path, master_packet, fake_cv_engine, fake_websocket):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    msg = {"type": "grade_submission", "request_id": "r3", "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    assert [m["type"] for m in fake_websocket.sent] == ["score_result"]
    assert fake_websocket.sent[0]["status"] == "error"
    assert "r3" not in server._pending_submissions


def test_oversized_image_rejected(tmp_db_path, master_packet, fake_cv_engine, fake_websocket, monkeypatch):
    monkeypatch.setattr(workers.websocket_server, "MAX_IMAGE_BYTES", 10)
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    big_b64 = base64.b64encode(b"x" * 100).decode()
    msg = {"type": "grade_submission", "request_id": "r4", "image_b64": big_b64,
           "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))
    assert fake_websocket.sent[-1]["status"] == "error"


def test_engine_error_replies_with_error_status_and_caches_nothing(tmp_db_path, master_packet, fake_cv_engine,
                                                                     fake_websocket, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_error(CVEngineError("simulated engine crash"))

    msg = {"type": "grade_submission", "request_id": "r6", "image_b64": fake_image_b64,
           "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    result = fake_websocket.sent[-1]
    assert result["status"] == "error"
    assert "simulated engine crash" in result["message"]
    assert "r6" not in server._pending_submissions


def test_engine_timeout_replies_with_error_status(tmp_db_path, master_packet, fake_cv_engine,
                                                    fake_websocket, fake_image_b64, monkeypatch):
    monkeypatch.setattr(workers.websocket_server, "CV_TIMEOUT_SECONDS", 0.05)
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_sleep(0.3, exam_result())

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


def test_no_ocr_id_still_grades_but_flags_review(tmp_db_path, master_packet, fake_cv_engine,
                                                   fake_websocket, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result(student_id=None))

    msg = {"type": "grade_submission", "request_id": "r9", "image_b64": fake_image_b64,
           "answer_version": "A", "essay_total": 0.0}
    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", msg))

    result = fake_websocket.sent[-1]
    assert result["status"] == "graded"
    assert result["student_id"] is None
    assert result["id_needs_review"] is True


# ----------------------------------------------------------------------
# Phase 2 (confirm_submission): the actual save
# ----------------------------------------------------------------------

def test_confirm_submission_saves_and_replies_success(tmp_db_path, master_packet, fake_cv_engine, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result())
    saved = []
    server.score_saved.connect(lambda sid, score: saved.append((sid, score)))

    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    graded, final = asyncio.run(grade_then_confirm(
        server, ws1, ws2, "Phone-1", grade_request_id="r10", confirm_request_id="r11",
        student_id="1234", group_type="morning", essay_total=10.0, image_b64=fake_image_b64,
    ))

    assert graded["status"] == "graded"
    assert final["status"] == "success"
    assert final["student_id"] == "1234"
    assert final["total_score"] == 18.0
    assert saved == [("1234", 18.0)]
    assert server.repo.get_existing_grade("1234")["score"] == 18.0
    assert "r10" not in server._pending_submissions


def test_confirm_submission_uses_corrected_student_id_not_ocr_guess(tmp_db_path, master_packet,
                                                                      fake_cv_engine, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result(student_id="1234"))  # OCR misread

    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    graded, final = asyncio.run(grade_then_confirm(
        server, ws1, ws2, "Phone-1", grade_request_id="r12", confirm_request_id="r13",
        student_id="5678",  # proctor's correction
        essay_total=0.0, image_b64=fake_image_b64,
    ))

    assert graded["student_id"] == "1234"  # what OCR read
    assert final["student_id"] == "5678"   # what actually got saved
    assert server.repo.get_existing_grade("5678") is not None
    assert server.repo.get_existing_grade("1234") is None


def test_confirm_submission_no_reprocessing_of_image(tmp_db_path, master_packet, fake_cv_engine, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result())

    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    asyncio.run(grade_then_confirm(
        server, ws1, ws2, "Phone-1", grade_request_id="r14", confirm_request_id="r15",
        student_id="1234", essay_total=0.0, image_b64=fake_image_b64,
    ))
    assert len(fake_cv_engine.calls) == 1


def test_confirm_submission_missing_id_reprompts_without_losing_cached_result(tmp_db_path, master_packet,
                                                                                fake_cv_engine, fake_websocket,
                                                                                fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result())

    asyncio.run(server._handle_grade_submission(fake_websocket, "Phone-1", {
        "type": "grade_submission", "request_id": "r16", "image_b64": fake_image_b64,
        "answer_version": "A", "essay_total": 0.0}))

    ws_bad = FakeWebSocket()
    asyncio.run(server._handle_confirm_submission(ws_bad, "Phone-1", {
        "type": "confirm_submission", "request_id": "r17", "original_request_id": "r16",
        "student_id": "", "group_type": None}))
    assert ws_bad.sent[-1]["status"] == "error"
    assert "r16" in server._pending_submissions

    ws_ok = FakeWebSocket()
    asyncio.run(server._handle_confirm_submission(ws_ok, "Phone-1", {
        "type": "confirm_submission", "request_id": "r18", "original_request_id": "r16",
        "student_id": "1234", "group_type": None}))
    assert ws_ok.sent[-1]["status"] == "success"
    assert len(fake_cv_engine.calls) == 1


def test_confirm_submission_for_expired_request_returns_error(tmp_db_path, master_packet, fake_cv_engine,
                                                                fake_websocket):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    asyncio.run(server._handle_confirm_submission(fake_websocket, "Phone-1", {
        "type": "confirm_submission", "request_id": "r19", "original_request_id": "does-not-exist",
        "student_id": "1234", "group_type": None}))
    assert fake_websocket.sent[-1]["status"] == "error"
    assert "No graded submission" in fake_websocket.sent[-1]["message"]


def test_confirm_submission_bumps_scan_count(tmp_db_path, master_packet, fake_cv_engine, fake_image_b64):
    from admin_dashboard.workers.websocket_server import ConnectedPhone
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result())
    with server._phones_lock:
        server.phones["Phone-1"] = ConnectedPhone(name="Phone-1", websocket=FakeWebSocket())

    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    asyncio.run(grade_then_confirm(
        server, ws1, ws2, "Phone-1", grade_request_id="r20", confirm_request_id="r21",
        student_id="1234", essay_total=0.0, image_b64=fake_image_b64,
    ))
    assert server.phones["Phone-1"].scan_count == 1


# ----------------------------------------------------------------------
# Duplicate detection at confirm time + resolution
# ----------------------------------------------------------------------

def test_confirming_a_known_student_id_flags_duplicate(tmp_db_path, master_packet, fake_cv_engine, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result(answers={1: "A", 2: "B", 4: "D", 5: "A"}))
    fake_cv_engine.queue_result(exam_result(answers={1: "A", 2: "C", 4: "D", 5: "A"}))

    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    asyncio.run(grade_then_confirm(
        server, ws1, ws2, "Phone-1", grade_request_id="r22", confirm_request_id="r23",
        student_id="1234", essay_total=0.0, image_b64=fake_image_b64,
    ))
    assert ws2.sent[-1]["status"] == "success"

    ws3, ws4 = FakeWebSocket(), FakeWebSocket()
    graded2, final2 = asyncio.run(grade_then_confirm(
        server, ws3, ws4, "Phone-2", grade_request_id="r24", confirm_request_id="r25",
        student_id="1234", essay_total=0.0, image_b64=fake_image_b64,
    ))

    assert final2["status"] == "duplicate"
    assert final2["previous"]["score"] == 8.0
    assert final2["incoming"]["score"] == 6.0
    assert "1234" in server._pending_duplicates


def test_id_corrected_to_a_different_existing_student_is_also_a_duplicate(tmp_db_path, master_packet,
                                                                            fake_cv_engine, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result(student_id="1234"))
    fake_cv_engine.queue_result(exam_result(student_id="9999"))

    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    asyncio.run(grade_then_confirm(
        server, ws1, ws2, "Phone-1", grade_request_id="r26", confirm_request_id="r27",
        student_id="1234", essay_total=0.0, image_b64=fake_image_b64,
    ))

    ws3, ws4 = FakeWebSocket(), FakeWebSocket()
    graded2, final2 = asyncio.run(grade_then_confirm(
        server, ws3, ws4, "Phone-2", grade_request_id="r28", confirm_request_id="r29",
        student_id="1234", essay_total=0.0, image_b64=fake_image_b64,
    ))
    assert graded2["student_id"] == "9999"
    assert final2["status"] == "duplicate"


def test_resolve_duplicate_overwrite_after_confirm(tmp_db_path, master_packet, fake_cv_engine, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result(answers={1: "A", 2: "B", 4: "D", 5: "A"}))
    fake_cv_engine.queue_result(exam_result(answers={1: "A", 2: "C", 4: "D", 5: "A"}))

    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    asyncio.run(grade_then_confirm(server, ws1, ws2, "Phone-1", grade_request_id="r30",
                                    confirm_request_id="r31", student_id="1234", essay_total=0.0,
                                    image_b64=fake_image_b64))
    ws3, ws4 = FakeWebSocket(), FakeWebSocket()
    asyncio.run(grade_then_confirm(server, ws3, ws4, "Phone-2", grade_request_id="r32",
                                    confirm_request_id="r33", student_id="1234", essay_total=0.0,
                                    image_b64=fake_image_b64))

    calls_before = len(fake_cv_engine.calls)
    resolve_ws = FakeWebSocket()
    asyncio.run(server._handle_resolve_duplicate(resolve_ws, "Phone-2", {
        "type": "resolve_duplicate", "student_id": "1234", "action": "overwrite", "request_id": "r34"}))

    assert len(fake_cv_engine.calls) == calls_before
    assert resolve_ws.sent[-1]["status"] == "success"
    assert server.repo.get_existing_grade("1234")["score"] == 6.0


def test_resolve_duplicate_discard_both_after_confirm(tmp_db_path, master_packet, fake_cv_engine, fake_image_b64):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    fake_cv_engine.queue_result(exam_result())
    fake_cv_engine.queue_result(exam_result())

    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    asyncio.run(grade_then_confirm(server, ws1, ws2, "Phone-1", grade_request_id="r35",
                                    confirm_request_id="r36", student_id="1234", essay_total=0.0,
                                    image_b64=fake_image_b64))
    ws3, ws4 = FakeWebSocket(), FakeWebSocket()
    asyncio.run(grade_then_confirm(server, ws3, ws4, "Phone-2", grade_request_id="r37",
                                    confirm_request_id="r38", student_id="1234", essay_total=0.0,
                                    image_b64=fake_image_b64))

    removed = []
    server.score_removed.connect(lambda sid: removed.append(sid))
    resolve_ws = FakeWebSocket()
    asyncio.run(server._handle_resolve_duplicate(resolve_ws, "Phone-2", {
        "type": "resolve_duplicate", "student_id": "1234", "action": "discard_both", "request_id": "r39"}))

    assert resolve_ws.sent[-1]["status"] == "success"
    assert server.repo.get_existing_grade("1234") is None
    assert removed == ["1234"]


def test_resolve_duplicate_for_unknown_student_returns_error(tmp_db_path, master_packet, fake_cv_engine,
                                                               fake_websocket):
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)
    asyncio.run(server._handle_resolve_duplicate(fake_websocket, "Phone-1", {
        "type": "resolve_duplicate", "student_id": "9999", "action": "overwrite", "request_id": "r40"}))
    assert fake_websocket.sent[-1]["status"] == "error"
    assert "No pending duplicate" in fake_websocket.sent[-1]["message"]


# ----------------------------------------------------------------------
# Stale entry expiry (heartbeat sweep) — both caches
# ----------------------------------------------------------------------

def test_stale_pending_duplicate_expires_via_sweep(tmp_db_path, master_packet, fake_cv_engine, monkeypatch):
    monkeypatch.setattr(workers.websocket_server, "HEARTBEAT_SWEEP_INTERVAL", 0.01)
    monkeypatch.setattr(workers.websocket_server, "STALE_DUPLICATE_SECONDS", 0)
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)

    async def run_one_sweep_iteration():
        server._pending_duplicates["1234"] = {
            "payload": {}, "phone_name": "Phone-1", "request_id": "r41",
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


def test_stale_pending_submission_expires_via_sweep(tmp_db_path, master_packet, fake_cv_engine, monkeypatch):
    monkeypatch.setattr(workers.websocket_server, "HEARTBEAT_SWEEP_INTERVAL", 0.01)
    monkeypatch.setattr(workers.websocket_server, "STALE_SUBMISSION_SECONDS", 0)
    server = make_server(tmp_db_path, master_packet, fake_cv_engine)

    async def run_one_sweep_iteration():
        server._pending_submissions["r42"] = {
            "result": {}, "phone_name": "Phone-1",
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
    assert "r42" not in server._pending_submissions
