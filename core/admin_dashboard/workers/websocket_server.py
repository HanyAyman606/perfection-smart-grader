"""
workers/websocket_server.py
-----------------------------
Replaces the old raw-socket ServerWorker entirely. Runs a `websockets`
asyncio server inside a QThread (same pattern the app already used),
speaking the JSON message contract in NEXUS_EDGE_SPEC.md.

Responsibilities:
  - Accept phone connections, require {"type": "auth", "name", "password"}
    as the first message before anything else is processed.
  - Track connected phones (name -> ConnectedPhone) for the dashboard's
    live-monitor UI: connected/disconnected status, reconnect rebinding,
    duplicate-name rejection, per-phone scan count, last-seen timestamp.
  - A background sweep task marks a phone "disconnected" if nothing has
    been heard from it (ping or otherwise) within HEARTBEAT_TIMEOUT_SECONDS
    — catches a frozen/backgrounded phone holding a dead-looking socket,
    not just a clean TCP close.
  - Reply to auth success with {"type": "auth_result", "status": "success",
    "master_packet": {...}}.
  - Handle {"type": "ping"} -> {"type": "pong"}.
  - Handle {"type": "submit_score"} / {"type": "resolve_duplicate"} against
    GradingRepository (grades table inside the active workspace's roster.db).
"""

import asyncio
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import websockets

from PySide6.QtCore import QThread, Signal

from admin_dashboard.grading_repository import GradingRepository

SYNC_PORT = 8765
DEFAULT_SESSION_PASSWORD = "12345678"  # fallback if no custom password saved yet
HEARTBEAT_TIMEOUT_SECONDS = 30   # no traffic within this window -> mark disconnected
HEARTBEAT_SWEEP_INTERVAL = 10    # how often the sweep task checks


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
    master packet, the active workspace's roster.db path, and a fresh
    session_id (grading_repository.new_session_id()) minted once per
    'Start Live Grading' click.

    Signals:
      log_signal(str)             — human-readable line for the monitor log
      phone_connected(str)        — proctor name
      phone_disconnected(str)     — proctor name
      score_saved(str, float)     — student_id, total_score
    """

    log_signal = Signal(str)
    phone_connected = Signal(str)
    phone_disconnected = Signal(str)
    score_saved = Signal(str, float)
    score_removed = Signal(str)

    def __init__(self, packet_data: dict, db_path: str, session_id: str, group_name: str,
                 session_password: str = DEFAULT_SESSION_PASSWORD):
        super().__init__()
        self.packet_data = packet_data
        self.session_id = session_id
        self.session_password = session_password
        self.repo = GradingRepository(db_path, session_id)
        self.repo.start_session(group_name)

        self.phones: dict[str, ConnectedPhone] = {}
        self._phones_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._server = None

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

    def stop(self):
        """Call from the Qt thread (e.g. 'STOP SERVER' button) for a clean
        asyncio shutdown, replacing the old QThread.terminate() force-kill.
        After calling this, still call .wait() to block until the thread
        actually exits, same as before."""
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

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
    # Heartbeat sweep — catches phones that never sent a close frame
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

        elif msg_type == "submit_score":
            await self._handle_submit_score(websocket, phone_name, msg)

        elif msg_type == "resolve_duplicate":
            await self._handle_resolve_duplicate(websocket, phone_name, msg)

        # Unknown types are ignored, not fatal — keeps the server
        # forward-compatible with phone-side additions.

    async def _handle_submit_score(self, websocket, phone_name: str, msg: dict):
        student_id = msg.get("student_id")
        # The phone correlates a response to the request that triggered it
        # purely by this field (see WebSocketClient.submitScoreAndAwait on
        # the client) — every score_result we send back MUST echo it, or
        # the phone's completer never resolves and it times out even
        # though we saved successfully.
        request_id = msg.get("request_id")

        try:
            existing = self.repo.get_existing_grade(student_id)

            if existing is not None:
                await websocket.send(json.dumps({
                    "type": "score_result",
                    "status": "duplicate",
                    "student_id": student_id,
                    "request_id": request_id,
                    "previous": {
                        "score": existing["score"],
                        "answer_version": existing["answer_version"],
                        "timestamp": existing["timestamp"],
                    },
                    "incoming": {
                        "score": msg.get("total_score"),
                        "answer_version": msg.get("answer_version"),
                        "timestamp": msg.get("timestamp"),
                    },
                }))
                return

            self.repo.save_grade(
                student_id=student_id,
                mcq_score=msg.get("mcq_score", 0.0),
                essay_total=msg.get("essay_total", 0.0),
                total_score=msg.get("total_score", 0.0),
                mistakes=msg.get("mistakes", []),
                answer_version=msg.get("answer_version"),
                group_type=msg.get("group_type"),
            )
        except Exception as e:
            # Without this, an exception here (locked DB, bad payload,
            # etc.) kills the coroutine silently: no score_result is ever
            # sent, and the phone's submitScoreAndAwait just sits there
            # until its own 10s timeout fires and shows a misleading
            # "network timeout" error. Always answer back.
            self.log_signal.emit(f"SUBMIT ERROR: {student_id} via {phone_name} -> {e}")
            await websocket.send(json.dumps({
                "type": "score_result",
                "status": "error",
                "student_id": student_id,
                "request_id": request_id,
                "message": f"Server failed to save grade: {e}",
            }))
            return

        self._bump_scan_count(phone_name)

        await websocket.send(json.dumps({
            "type": "score_result",
            "status": "success",
            "student_id": student_id,
            "request_id": request_id,
            "message": "Grade saved successfully.",
        }))
        self.log_signal.emit(f"SAVED: {student_id} ({msg.get('total_score')} pts) via {phone_name}")
        self.score_saved.emit(student_id, float(msg.get("total_score", 0.0)))

    async def _handle_resolve_duplicate(self, websocket, phone_name: str, msg: dict):
        student_id = msg.get("student_id")
        action = msg.get("action")
        request_id = msg.get("request_id")  # must be echoed back — see _handle_submit_score

        try:
            if action == "overwrite":
                payload = msg.get("new_score_payload") or {}
                self.repo.overwrite_grade(
                    student_id=student_id,
                    mcq_score=payload.get("mcq_score", 0.0),
                    essay_total=payload.get("essay_total", 0.0),
                    total_score=payload.get("total_score", 0.0),
                    mistakes=payload.get("mistakes", []),
                    answer_version=payload.get("answer_version"),
                    group_type=payload.get("group_type"),
                )
                self._bump_scan_count(phone_name)
                self.score_saved.emit(student_id, float(payload.get("total_score", 0.0)))

            elif action == "discard_both":
                self.repo.discard_grade(student_id)
                self.score_removed.emit(student_id)

                # "keep_previous" -> no DB action.
        except Exception as e:
            # Same reasoning as _handle_submit_score: always answer back
            # so resolveDuplicateAndAwait on the phone doesn't just time out.
            self.log_signal.emit(f"RESOLVE ERROR: {student_id} -> {action} via {phone_name} -> {e}")
            await websocket.send(json.dumps({
                "type": "resolve_duplicate_result",
                "status": "error",
                "student_id": student_id,
                "request_id": request_id,
                "message": f"Server failed to resolve duplicate: {e}",
            }))
            return

        await websocket.send(json.dumps({
            "type": "resolve_duplicate_result",
            "status": "success",
            "student_id": student_id,
            "request_id": request_id,
            "action_taken": action,
        }))
        self.log_signal.emit(f"DUPLICATE RESOLVED: {student_id} -> {action} (by {phone_name})")

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