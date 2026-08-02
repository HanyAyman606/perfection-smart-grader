"""
Aggressive/adversarial test pass for WebSocketServer — concurrent phones,
bad auth, malformed messages, and a real race-condition check on
UNIQUE(session_id, student_id) under simultaneous submits for the same
student. Complements test_live_grading.py's happy-path check.

Run: python test_stress.py
Close the real app first — this binds the real port 8765.
"""

import asyncio
import json
import shutil
import sys
import tempfile

from PySide6.QtCore import QCoreApplication, QTimer

from admin_dashboard.project_manager import ProjectManager
from admin_dashboard.grading_repository import GradingRepository, new_session_id
from admin_dashboard.workers.websocket_server import WebSocketServer, SYNC_PORT

import websockets

FAILURES = []


def check(condition, label):
    mark = "✔" if condition else "✘"
    print(f"{mark} {label}")
    if not condition:
        FAILURES.append(label)


async def scenario_wrong_password():
    async with websockets.connect(f"ws://localhost:{SYNC_PORT}") as ws:
        await ws.send(json.dumps({"type": "auth", "name": "Bad-Phone", "password": "wrong"}))
        result = json.loads(await ws.recv())
        check(result["status"] == "error", "Wrong password rejected")


async def scenario_missing_name():
    async with websockets.connect(f"ws://localhost:{SYNC_PORT}") as ws:
        await ws.send(json.dumps({"type": "auth", "name": "", "password": "12345678"}))
        result = json.loads(await ws.recv())
        check(result["status"] == "error", "Empty name rejected")


async def scenario_malformed_json():
    """Server should survive garbage input, not crash the whole thread."""
    async with websockets.connect(f"ws://localhost:{SYNC_PORT}") as ws:
        await ws.send(json.dumps({"type": "auth", "name": "Malformed-Tester", "password": "12345678"}))
        auth = json.loads(await ws.recv())
        check(auth["status"] == "success", "Auth succeeded before malformed-message test")

        await ws.send("{not valid json at all")
        # Server should silently ignore it (per _route_message's json.JSONDecodeError
        # handling) rather than closing the connection — prove the socket is
        # still alive by sending a real ping right after.
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await ws.recv())
        check(pong["type"] == "pong", "Connection survived malformed JSON, still responds to ping")


async def scenario_duplicate_name_rejected():
    """Two different sockets claiming the same phone name while the first
    is still connected — second should be rejected, not silently take over."""
    ws1 = await websockets.connect(f"ws://localhost:{SYNC_PORT}")
    await ws1.send(json.dumps({"type": "auth", "name": "Duplicate-Name-Phone", "password": "12345678"}))
    result1 = json.loads(await ws1.recv())
    check(result1["status"] == "success", "First connection with name succeeds")

    async with websockets.connect(f"ws://localhost:{SYNC_PORT}") as ws2:
        await ws2.send(json.dumps({"type": "auth", "name": "Duplicate-Name-Phone", "password": "12345678"}))
        result2 = json.loads(await ws2.recv())
        check(result2["status"] == "error", "Second connection with same name (while first still connected) rejected")

    await ws1.close()


async def scenario_reconnect_same_name_after_disconnect():
    """Once the FIRST connection actually closes, the name should become
    available again — proves status flips to 'disconnected', not stuck
    'connected' forever."""
    ws1 = await websockets.connect(f"ws://localhost:{SYNC_PORT}")
    await ws1.send(json.dumps({"type": "auth", "name": "Reconnect-Phone", "password": "12345678"}))
    await ws1.recv()
    await ws1.close()
    await asyncio.sleep(0.3)  # let the server's finally-block mark it disconnected

    async with websockets.connect(f"ws://localhost:{SYNC_PORT}") as ws2:
        await ws2.send(json.dumps({"type": "auth", "name": "Reconnect-Phone", "password": "12345678"}))
        result2 = json.loads(await ws2.recv())
        check(result2["status"] == "success", "Same name reconnects successfully after first socket closed")


async def scenario_concurrent_phones_distinct_students(count=20):
    """N phones connecting and submitting distinct students AT THE SAME
    TIME — checks the server doesn't drop, block, or serialize badly
    under real concurrent load."""
    async def one_phone(i):
        async with websockets.connect(f"ws://localhost:{SYNC_PORT}") as ws:
            await ws.send(json.dumps({"type": "auth", "name": f"Stress-Phone-{i}", "password": "12345678"}))
            auth = json.loads(await ws.recv())
            if auth["status"] != "success":
                return False
            await ws.send(json.dumps({
                "type": "submit_score", "student_id": f"5{i:03d}",
                "mcq_score": 5.0, "essay_total": 0.0, "total_score": 5.0,
                "mistakes": [], "answer_version": "A", "group_type": "M",
            }))
            result = json.loads(await ws.recv())
            return result["status"] == "success"

    results = await asyncio.gather(*(one_phone(i) for i in range(count)))
    check(all(results), f"All {count} concurrent phones authed + submitted distinct students successfully")


async def scenario_concurrent_same_student_race(db_path, session_id, count=10):
    """The real stress test: N connections racing to submit_score for the
    SAME student_id at the exact same time. UNIQUE(session_id, student_id)
    means at most one INSERT can win — this proves the others don't crash
    the server thread or silently corrupt state, they should each get a
    clean success/duplicate/error response and the DB should end up with
    exactly one row."""
    async def racer(i):
        try:
            async with websockets.connect(f"ws://localhost:{SYNC_PORT}") as ws:
                await ws.send(json.dumps({"type": "auth", "name": f"Racer-{i}", "password": "12345678"}))
                await ws.recv()
                await ws.send(json.dumps({
                    "type": "submit_score", "student_id": "9999-RACE",
                    "mcq_score": 4.0, "essay_total": 0.0, "total_score": 4.0,
                    "mistakes": [], "answer_version": "A", "group_type": "W",
                }))
                result = json.loads(await ws.recv())
                return result["status"]
        except Exception as e:
            return f"EXCEPTION: {e}"

    outcomes = await asyncio.gather(*(racer(i) for i in range(count)))
    print("   race outcomes:", outcomes)
    check(all(o in ("success", "duplicate") for o in outcomes), "No exceptions/crashes under concurrent same-student race")

    # Verify exactly ONE row actually landed for that student, regardless
    # of how many racers thought they succeeded.
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT COUNT(*) FROM grades WHERE session_id = ? AND student_id = ?",
        (session_id, "9999-RACE"),
    )
    row_count = cursor.fetchone()[0]
    conn.close()
    check(row_count == 1, f"Exactly one DB row for the raced student (found {row_count}) — UNIQUE constraint held")


async def run_all(db_path, session_id):
    await scenario_wrong_password()
    await scenario_missing_name()
    await scenario_malformed_json()
    await scenario_duplicate_name_rejected()
    await scenario_reconnect_same_name_after_disconnect()
    await scenario_concurrent_phones_distinct_students(count=20)
    await scenario_concurrent_same_student_race(db_path, session_id, count=10)


def main():
    tmp_dir = tempfile.mkdtemp(prefix="nexus_stress_test_")
    try:
        app = QCoreApplication(sys.argv)

        pm = ProjectManager()
        pm.create_project("Stress Test Exam", tmp_dir)
        pm.set_session_password("12345678")

        session_id = new_session_id()
        server = WebSocketServer(
            packet_data={"exam_name": "Stress Test Exam", "mcq_count": 10},
            db_path=pm.db_path, session_id=session_id,
            group_name="Stress Group", session_password="12345678",
        )
        server.log_signal.connect(lambda msg: print("[SERVER LOG]", msg))
        server.start()

        def _start_scenarios():
            async def _wrapper():
                try:
                    await run_all(pm.db_path, session_id)
                finally:
                    server.stop()
                    server.wait()
                    app.quit()
            asyncio.run(_wrapper())

        QTimer.singleShot(500, _start_scenarios)
        app.exec()

        print(f"\n{'='*50}")
        if FAILURES:
            print(f"✘✘✘ {len(FAILURES)} FAILURE(S):")
            for f in FAILURES:
                print("   -", f)
        else:
            print("✔✔✔ ALL STRESS SCENARIOS PASSED.")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()