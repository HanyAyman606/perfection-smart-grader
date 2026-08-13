"""
workers/websocket_server.py
-----------------------------
Replaces the old raw-socket ServerWorker entirely. Runs a `websockets`
asyncio server inside a QThread (same pattern the app already used),
speaking the JSON message contract in NEXUS_EDGE_SPEC.md.

ARCHITECTURE CHANGE: grading now happens entirely on the lab machine.
The phone no longer runs OpenCV/ONNX — it captures a photo, sends the
raw image + manually-entered essay marks, and this server runs the same
cv_engine (via ctypes) that used to run on-device, scores it against the
master_packet, and sends the result back. This removes the 4-5s
on-device stall per phone and lets N phones' scans be processed
concurrently, bounded by the lab CPU's core count instead of by each
phone's own CPU.

TWO-PHASE COMMIT: grading and saving are deliberately two separate
round trips, not one:
  Phase 1 — grade_submission: phone sends the image + essay marks.
    Server runs CV/ONNX + scoring and replies with status "graded"
    (mcq_score, mistakes, OCR'd student_id, id_needs_review) — but does
    NOT save anything yet. The computed result is cached server-side,
    keyed by request_id, in _pending_submissions.
  Phase 2 — confirm_submission: phone shows the proctor the graded
    result, lets them correct the OCR'd student_id/group_type if
    needed, then sends confirm_submission referencing the original
    request_id plus the (possibly corrected) student_id/group_type.
    THIS is what actually writes the row to the db.
Duplicate-student-id detection intentionally happens at confirm time,
not grade time — the id can change between phases (proctor correcting
a bad OCR read), so checking for a duplicate before that correction
would be checking the wrong id. A duplicate found at confirm goes
through the exact same overwrite/keep/discard flow as before, just
reached via confirm_submission instead of grade_submission.

Responsibilities:
  - Accept phone connections, require {"type": "auth", "name", "password"}
    as the first message before anything else is processed.
  - Track connected phones (name -> ConnectedPhone) for the dashboard's
    live-monitor UI: connected/disconnected status, reconnect rebinding,
    duplicate-name rejection, per-phone scan count, last-seen timestamp.
  - A background sweep task marks a phone "disconnected" if nothing has
    been heard from it (ping or otherwise) within HEARTBEAT_TIMEOUT_SECONDS
    — catches a frozen/backgrounded phone holding a dead-looking socket,
    not just a clean TCP close. The same sweep also expires any
    graded-but-unconfirmed submission sitting in _pending_submissions,
    and any duplicate sitting in _pending_duplicates, that the phone
    never followed up on.
  - Reply to auth success with {"type": "auth_result", "status": "success",
    "master_packet": {...}}.
  - Handle {"type": "ping"} -> {"type": "pong"}.
  - Handle {"type": "grade_submission"} (phase 1): decode the image,
    hand it to the cv_engine (via a bounded ThreadPoolExecutor so
    CPU-bound work never blocks the asyncio event loop / other phones),
    score it, cache the result, and reply with score_result — status is
    one of "graded" (awaiting confirm_submission), "needs_retake"
    (blurry/unreadable scan), or "error".
  - Handle {"type": "confirm_submission"} (phase 2): look up the cached
    graded result, save it via GradingRepository (grades table inside
    the active workspace's roster.db) under the proctor-confirmed
    student_id/group_type, and reply with score_result — status is one
    of "success", "duplicate" (student_id already graded this session),
    or "error".
  - Handle {"type": "resolve_duplicate"}: phone's decision on a pending
    duplicate (overwrite / discard_both / keep_previous). Uses the
    server-cached computed result from _pending_duplicates rather than
    asking the phone to resend score data it no longer computes itself.
"""

import asyncio
import base64
import json
import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import websockets

from PySide6.QtCore import QThread, Signal

from admin_dashboard.grading_repository import GradingRepository
from admin_dashboard.cv_engine_bridge import CVEngineBridge, CVEngineError
from admin_dashboard.mcq_scoring import score_mcq

SYNC_PORT = 8765
DEFAULT_SESSION_PASSWORD = "12345678"  # fallback if no custom password saved yet
HEARTBEAT_TIMEOUT_SECONDS = 30   # no traffic within this window -> mark disconnected
HEARTBEAT_SWEEP_INTERVAL = 10    # how often the sweep task checks
STALE_DUPLICATE_SECONDS = 300    # a pending duplicate nobody resolved gets dropped, not leaked forever
STALE_SUBMISSION_SECONDS = 300   # a graded-but-never-confirmed submission gets dropped the same way
CV_TIMEOUT_SECONDS = 20          # a poisoned/corrupt image must not permanently eat a worker slot
MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15MB decoded — generous for a phone photo, rejects garbage payloads early


@dataclass
class ConnectedPhone:
    name: str
    websocket: object
    status: str = "connected"          # "connected" | "disconnected"
    connected_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_seen_monotonic: float = 0.0   # perf_counter-based, used only for timeout math
    last_seen: str = field(default_factory=lambda: datetime.now().isoformat())
    scan_count: int = 0

    def touch(self, now_monotonic: float):
        self.last_seen_monotonic = now_monotonic
        self.last_seen = datetime.now().isoformat()


class WebSocketServer(QThread):
    """Drop-in replacement for the old ServerWorker. Construct with the
    master packet, the active workspace's roster.db path, a fresh
    session_id (grading_repository.new_session_id()) minted once per
    'Start Live Grading' click, and a CVEngineBridge instance (built once
    by LiveSessionController and shared for the life of the session).

    Signals:
      log_signal(str)             — human-readable line for the monitor log
      phone_connected(str)        — proctor name
      phone_disconnected(str)     — proctor name
      score_saved(str, float)     — student_id, total_score
      score_removed(str)          — student_id
    """

    log_signal = Signal(str)
    phone_connected = Signal(str)
    phone_disconnected = Signal(str)
    score_saved = Signal(str, float)
    score_removed = Signal(str)

    def __init__(self, packet_data: dict, db_path: str, session_id: str, group_name: str,
                 cv_engine: CVEngineBridge, session_password: str = DEFAULT_SESSION_PASSWORD,
                 max_cv_workers: Optional[int] = None):
        super().__init__()
        self.packet_data = packet_data
        self.session_id = session_id
        self.session_password = session_password
        self.repo = GradingRepository(db_path, session_id)
        self.repo.start_session(group_name)

        self.cv_engine = cv_engine
        self._cv_config_kwargs = cv_engine.config_kwargs_from_master_packet(packet_data)
        # Bounded, shared pool — NOT one thread per device. Leaving one
        # core free keeps the Qt/asyncio side (and the rest of the OS)
        # responsive under sustained load from many phones at once.
        worker_count = max_cv_workers or max(1, (os.cpu_count() or 2) - 1)
        self._cv_executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="cv-worker")

        self.phones: dict[str, ConnectedPhone] = {}
        self._phones_lock = threading.Lock()

        # student_id -> {"payload": dict, "phone_name": str, "request_id": str, "created_at": float}
        # Caches a computed-but-not-yet-saved grade while a phone decides
        # what to do about a duplicate, so resolving it never requires
        # re-running OpenCV/ONNX or trusting the phone to resend a score
        # it no longer computes itself.
        self._pending_duplicates: dict[str, dict] = {}

        # request_id -> {"result": dict, "phone_name": str, "created_at": float}
        # Phase-1 (grade_submission) output, cached until the phone sends
        # confirm_submission with the proctor-reviewed student_id/group_type.
        # Nothing here is saved to the db yet.
        self._pending_submissions: dict[str, dict] = {}

        self._pending_lock = threading.Lock()

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._server = None
        self._scan_tmp_dir = tempfile.mkdtemp(prefix="nexus_scans_")

    # ------------------------------------------------------------------
    # QThread entry point
    # ------------------------------------------------------------------
    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as e:
            self.log_signal.emit(f"Server Error: {e}")
        finally:
            self._loop.close()
            self._cv_executor.shutdown(wait=False, cancel_futures=True)
            self._cleanup_tmp_dir()

    def stop(self):
        """Call from the Qt thread (e.g. 'STOP SERVER' button) for a clean
        asyncio shutdown, replacing the old QThread.terminate() force-kill.
        After calling this, still call .wait() to block until the thread
        actually exits, same as before."""
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def _cleanup_tmp_dir(self):
        try:
            for fname in os.listdir(self._scan_tmp_dir):
                try:
                    os.remove(os.path.join(self._scan_tmp_dir, fname))
                except OSError:
                    pass
            os.rmdir(self._scan_tmp_dir)
        except OSError:
            pass

    async def _serve(self):
        self._server = await websockets.serve(self._handle_connection, "0.0.0.0", SYNC_PORT)
        sweep_task = asyncio.ensure_future(self._heartbeat_sweep_loop())

        self.log_signal.emit(f"SERVER ONLINE: Listening on port {SYNC_PORT}...")
        await self._stop_event.wait()

        sweep_task.cancel()
        self._server.close()
        await self._server.wait_closed()
        self.log_signal.emit("SERVER SHUTDOWN.")

    # ------------------------------------------------------------------
    # Heartbeat sweep — catches phones that never sent a close frame,
    # and expires abandoned duplicate-resolution prompts.
    # ------------------------------------------------------------------
    async def _heartbeat_sweep_loop(self):
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_SWEEP_INTERVAL)
                now = asyncio.get_event_loop().time()

                stale_names = []
                with self._phones_lock:
                    for phone in self.phones.values():
                        if phone.status == "connected" and (now - phone.last_seen_monotonic) > HEARTBEAT_TIMEOUT_SECONDS:
                            phone.status = "disconnected"
                            stale_names.append(phone.name)
                for name in stale_names:
                    self.log_signal.emit(f"TIMEOUT: {name} (no heartbeat)")
                    self.phone_disconnected.emit(name)

                expired_ids = []
                expired_request_ids = []
                with self._pending_lock:
                    for sid, pending in list(self._pending_duplicates.items()):
                        if now - pending["created_at"] > STALE_DUPLICATE_SECONDS:
                            expired_ids.append(sid)
                            del self._pending_duplicates[sid]
                    for rid, pending in list(self._pending_submissions.items()):
                        if now - pending["created_at"] > STALE_SUBMISSION_SECONDS:
                            expired_request_ids.append(rid)
                            del self._pending_submissions[rid]
                for sid in expired_ids:
                    self.log_signal.emit(f"DUPLICATE PROMPT EXPIRED: {sid} (no response from phone)")
                for rid in expired_request_ids:
                    self.log_signal.emit(f"GRADED SUBMISSION EXPIRED: request {rid} (never confirmed)")
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Per-connection handler
    # ------------------------------------------------------------------
    async def _handle_connection(self, websocket):
        phone_name = None
        try:
            phone_name = await self._authenticate(websocket)
            if phone_name is None:
                return

            async for raw_message in websocket:
                await self._route_message(websocket, phone_name, raw_message)

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if phone_name:
                self._mark_disconnected(phone_name)

    async def _authenticate(self, websocket) -> Optional[str]:
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=15)
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            return None

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.close()
            return None

        if msg.get("type") != "auth":
            await websocket.close()
            return None

        name = (msg.get("name") or "").strip()
        password = msg.get("password") or ""

        if password != self.session_password or not name:
            await websocket.send(json.dumps({
                "type": "auth_result",
                "status": "error",
                "message": "Incorrect password." if name else "Name is required.",
            }))
            await websocket.close()
            return None

        now = asyncio.get_event_loop().time()
        with self._phones_lock:
            existing = self.phones.get(name)
            if existing is not None and existing.status == "connected":
                await websocket.send(json.dumps({
                    "type": "auth_result",
                    "status": "error",
                    "message": f"'{name}' is already connected from another device.",
                }))
                await websocket.close()
                return None

            if existing is not None:
                existing.websocket = websocket
                existing.status = "connected"
                existing.touch(now)
            else:
                phone = ConnectedPhone(name=name, websocket=websocket)
                phone.touch(now)
                self.phones[name] = phone

        await websocket.send(json.dumps({
            "type": "auth_result",
            "status": "success",
            "master_packet": self.packet_data,
        }))
        self.log_signal.emit(f"CONNECTED: {name}")
        self.phone_connected.emit(name)
        return name

    def _mark_disconnected(self, name: str):
        with self._phones_lock:
            phone = self.phones.get(name)
            if phone:
                phone.status = "disconnected"
        self.log_signal.emit(f"DISCONNECTED: {name}")
        self.phone_disconnected.emit(name)

    def _touch_phone(self, name: str):
        now = asyncio.get_event_loop().time()
        with self._phones_lock:
            phone = self.phones.get(name)
            if phone:
                phone.touch(now)

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------
    async def _route_message(self, websocket, phone_name: str, raw_message: str):
        self._touch_phone(phone_name)

        try:
            msg = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")

        if msg_type == "ping":
            await websocket.send(json.dumps({"type": "pong"}))

        elif msg_type == "grade_submission":
            await self._handle_grade_submission(websocket, phone_name, msg)

        elif msg_type == "confirm_submission":
            await self._handle_confirm_submission(websocket, phone_name, msg)

        elif msg_type == "resolve_duplicate":
            await self._handle_resolve_duplicate(websocket, phone_name, msg)

        # Unknown types are ignored, not fatal — keeps the server
        # forward-compatible with phone-side additions.

    # ------------------------------------------------------------------
    # Lab-side grading pipeline
    # ------------------------------------------------------------------
    def _decode_and_validate_image(self, image_b64: Optional[str]) -> Optional[bytes]:
        if not image_b64:
            return None
        try:
            data = base64.b64decode(image_b64, validate=True)
        except Exception:
            return None
        if not data or len(data) > MAX_IMAGE_BYTES:
            return None
        return data

    def _write_temp_scan(self, image_bytes: bytes) -> str:
        # uuid-based filename, never derived from student_id: two phones
        # can be mid-scan of the same student before either has an OCR'd
        # ID yet, and a name collision there would corrupt one request's
        # image out from under the other's in-flight process_exam call.
        path = os.path.join(self._scan_tmp_dir, f"{uuid.uuid4().hex}.jpg")
        with open(path, "wb") as f:
            f.write(image_bytes)
        return path

    async def _handle_grade_submission(self, websocket, phone_name: str, msg: dict):
        request_id = msg.get("request_id")
        student_id_hint = (msg.get("student_id") or "").strip() or None  # manual-entry fallback if OCR ID read fails
        answer_version = msg.get("answer_version")
        essay_total = msg.get("essay_total", 0.0)
        group_type = msg.get("group_type")

        image_bytes = self._decode_and_validate_image(msg.get("image_b64"))
        if image_bytes is None:
            await websocket.send(json.dumps({
                "type": "score_result", "status": "error", "request_id": request_id,
                "message": "Image missing, corrupt, or too large.",
            }))
            return

        # Cheap ack the instant we've got the image, before the (possibly
        # queued) CPU-bound work starts — lets the phone UI show
        # "processing…" honestly instead of silently waiting, especially
        # useful when several phones submitted at once and this one is
        # sitting in the executor's queue for a few seconds.
        await websocket.send(json.dumps({"type": "grade_ack", "request_id": request_id}))

        tmp_path = self._write_temp_scan(image_bytes)
        loop = asyncio.get_event_loop()

        try:
            exam_result = await asyncio.wait_for(
                loop.run_in_executor(
                    self._cv_executor, self._process_exam_sync, tmp_path
                ),
                timeout=CV_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            self.log_signal.emit(f"ENGINE TIMEOUT: {phone_name} (request {request_id})")
            await websocket.send(json.dumps({
                "type": "score_result", "status": "error", "request_id": request_id,
                "message": "Processing timed out — please retake the photo.",
            }))
            return
        except CVEngineError as e:
            self.log_signal.emit(f"ENGINE ERROR: {phone_name} (request {request_id}) -> {e}")
            await websocket.send(json.dumps({
                "type": "score_result", "status": "error", "request_id": request_id,
                "message": f"Engine failure: {e}",
            }))
            return
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        if exam_result.get("confidence") == "LOW":
            await websocket.send(json.dumps({
                "type": "score_result", "status": "needs_retake", "request_id": request_id,
                "warnings": exam_result.get("warnings", []),
            }))
            return

        ocr_student_id = exam_result.get("student_id")
        student_id_guess = ocr_student_id or student_id_hint
        id_needs_review = exam_result.get("id_needs_review", False) or not ocr_student_id

        mcq_score, mistakes = score_mcq(exam_result.get("questions", []), self.packet_data, answer_version)
        total_score = mcq_score + essay_total

        # Phase 1 ends here — compute and cache, but do NOT touch the db
        # yet. The proctor still needs to review/correct student_id and
        # group_type on the phone before this is final; that correction
        # arrives as confirm_submission below, referencing this request_id.
        with self._pending_lock:
            self._pending_submissions[request_id] = {
                "result": dict(
                    mcq_score=mcq_score, mistakes=mistakes, essay_total=essay_total,
                    total_score=total_score, answer_version=answer_version,
                    ocr_student_id=ocr_student_id, group_type=group_type,
                ),
                "phone_name": phone_name,
                "created_at": asyncio.get_event_loop().time(),
            }

        await websocket.send(json.dumps({
            "type": "score_result", "status": "graded", "request_id": request_id,
            "student_id": student_id_guess, "id_needs_review": id_needs_review,
            "mcq_score": mcq_score, "mistakes": mistakes, "essay_total": essay_total,
            "total_score": total_score, "group_type": group_type,
        }))
        self.log_signal.emit(f"GRADED: request {request_id} ({total_score} pts) via {phone_name}, awaiting confirmation")

    def _process_exam_sync(self, image_path: str) -> dict:
        """Runs on a worker thread via run_in_executor — must stay a
        plain blocking call, no asyncio inside it."""
        return self.cv_engine.process_exam(image_path, **self._cv_config_kwargs)

    async def _handle_confirm_submission(self, websocket, phone_name: str, msg: dict):
        """Phase 2 — the proctor has reviewed the phase-1 graded result
        (correcting student_id/group_type if the OCR read was wrong) and
        is now committing it. This is the only place a grade actually
        gets written to the db."""
        request_id = msg.get("request_id")
        original_request_id = msg.get("original_request_id")
        student_id = (msg.get("student_id") or "").strip()
        group_type = msg.get("group_type")

        with self._pending_lock:
            pending = self._pending_submissions.pop(original_request_id, None)

        if pending is None:
            await websocket.send(json.dumps({
                "type": "score_result", "status": "error", "request_id": request_id,
                "message": "No graded submission found for this request — it may have expired. Please retake and resubmit.",
            }))
            return

        if not student_id:
            # Put it back — the proctor can still fix the id and confirm
            # again without re-running CV/ONNX a second time.
            with self._pending_lock:
                pending["created_at"] = asyncio.get_event_loop().time()
                self._pending_submissions[original_request_id] = pending
            await websocket.send(json.dumps({
                "type": "score_result", "status": "error", "request_id": request_id,
                "message": "Student ID is required before confirming.",
            }))
            return

        result = pending["result"]
        payload = dict(
            student_id=student_id, mcq_score=result["mcq_score"], essay_total=result["essay_total"],
            total_score=result["total_score"], mistakes=result["mistakes"],
            answer_version=result["answer_version"], group_type=group_type or result["group_type"],
        )

        existing = self.repo.get_existing_grade(student_id)
        if existing is not None:
            with self._pending_lock:
                self._pending_duplicates[student_id] = {
                    "payload": payload,
                    "phone_name": phone_name,
                    "request_id": request_id,
                    "created_at": asyncio.get_event_loop().time(),
                }
            await websocket.send(json.dumps({
                "type": "score_result", "status": "duplicate", "student_id": student_id,
                "request_id": request_id,
                "previous": {
                    "score": existing["score"],
                    "answer_version": existing["answer_version"],
                    "timestamp": existing["timestamp"],
                },
                "incoming": {
                    "score": payload["total_score"], "answer_version": payload["answer_version"],
                    "mistakes": payload["mistakes"],
                },
            }))
            return

        try:
            self._finalize_grade(payload)
        except Exception as e:
            self.log_signal.emit(f"SAVE ERROR: {student_id} via {phone_name} -> {e}")
            await websocket.send(json.dumps({
                "type": "score_result", "status": "error", "request_id": request_id,
                "student_id": student_id, "message": f"Server failed to save grade: {e}",
            }))
            return

        self._bump_scan_count(phone_name)
        await websocket.send(json.dumps({
            "type": "score_result", "status": "success", "request_id": request_id,
            "student_id": student_id, "total_score": payload["total_score"], "mistakes": payload["mistakes"],
        }))
        self.log_signal.emit(f"SAVED: {student_id} ({payload['total_score']} pts) via {phone_name}")

    async def _handle_resolve_duplicate(self, websocket, phone_name: str, msg: dict):
        student_id = msg.get("student_id")
        action = msg.get("action")
        request_id = msg.get("request_id")  # must be echoed back, same as every other reply here

        with self._pending_lock:
            pending = self._pending_duplicates.pop(student_id, None)

        if pending is None:
            await websocket.send(json.dumps({
                "type": "resolve_duplicate_result", "status": "error", "student_id": student_id,
                "request_id": request_id,
                "message": "No pending duplicate found for this student — it may have expired or already been resolved.",
            }))
            return

        try:
            if action == "overwrite":
                self._finalize_grade(pending["payload"], overwrite=True)
                self._bump_scan_count(phone_name)

            elif action == "discard_both":
                self.repo.discard_grade(student_id)
                self.score_removed.emit(student_id)

            # "keep_previous" -> already popped from _pending_duplicates, no DB action needed.
        except Exception as e:
            self.log_signal.emit(f"RESOLVE ERROR: {student_id} -> {action} via {phone_name} -> {e}")
            await websocket.send(json.dumps({
                "type": "resolve_duplicate_result", "status": "error", "student_id": student_id,
                "request_id": request_id, "message": f"Server failed to resolve duplicate: {e}",
            }))
            return

        await websocket.send(json.dumps({
            "type": "resolve_duplicate_result", "status": "success", "student_id": student_id,
            "request_id": request_id, "action_taken": action,
        }))
        self.log_signal.emit(f"DUPLICATE RESOLVED: {student_id} -> {action} (by {phone_name})")

    def _finalize_grade(self, payload: dict, overwrite: bool = False):
        save = self.repo.overwrite_grade if overwrite else self.repo.save_grade
        save(
            student_id=payload["student_id"],
            mcq_score=payload["mcq_score"],
            essay_total=payload["essay_total"],
            total_score=payload["total_score"],
            mistakes=payload["mistakes"],
            answer_version=payload["answer_version"],
            group_type=payload["group_type"],
        )
        self.score_saved.emit(payload["student_id"], float(payload["total_score"]))

    def _bump_scan_count(self, phone_name: str):
        with self._phones_lock:
            phone = self.phones.get(phone_name)
            if phone:
                phone.scan_count += 1

    # ------------------------------------------------------------------
    # Read-only snapshot for the dashboard's live-monitor UI
    # ------------------------------------------------------------------
    def get_connected_phones_snapshot(self) -> list[dict]:
        with self._phones_lock:
            return [
                {
                    "name": p.name,
                    "status": p.status,
                    "connected_at": p.connected_at,
                    "last_seen": p.last_seen,
                    "scan_count": p.scan_count,
                }
                for p in self.phones.values()
            ]
