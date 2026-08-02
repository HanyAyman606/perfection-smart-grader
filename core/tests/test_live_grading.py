"""
End-to-end test of WebSocketServer against a real (local) websocket
client, simulating what a phone does: connect, auth, submit_score,
trigger a duplicate, resolve it. No PySide6 GUI needed — QCoreApplication
gives just enough Qt event loop for QThread's signals to work.

Run: python test_live_grading.py
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile

from PySide6.QtCore import QCoreApplication, QTimer

from admin_dashboard.project_manager import ProjectManager
from admin_dashboard.grading_repository import GradingRepository, new_session_id
from admin_dashboard.workers.websocket_server import WebSocketServer, SYNC_PORT

import websockets


async def simulate_phone():
    uri = f"ws://localhost:{SYNC_PORT}"
    async with websockets.connect(uri) as ws:
        # 1. Auth
        await ws.send(json.dumps({"type": "auth", "name": "Test-Phone-1", "password": "12345678"}))
        auth_result = json.loads(await ws.recv())
        assert auth_result["status"] == "success", f"auth failed: {auth_result}"
        print("✔ Auth succeeded, got master_packet with keys:", list(auth_result["master_packet"].keys()))

        # 2. Submit a fresh score
        await ws.send(json.dumps({
            "type": "submit_score", "student_id": "3001",
            "mcq_score": 8.0, "essay_total": 2.0, "total_score": 10.0,
            "mistakes": [], "answer_version": "A", "group_type": "M",
        }))
        result = json.loads(await ws.recv())
        assert result["status"] == "success", f"submit failed: {result}"
        print("✔ First submit_score succeeded:", result)

        # 3. Submit the SAME student again -> should come back as a duplicate
        await ws.send(json.dumps({
            "type": "submit_score", "student_id": "3001",
            "mcq_score": 7.0, "essay_total": 2.0, "total_score": 9.0,
            "mistakes": [], "answer_version": "A", "group_type": "M",
        }))
        dup_result = json.loads(await ws.recv())
        assert dup_result["status"] == "duplicate", f"expected duplicate, got {dup_result}"
        print("✔ Second submit correctly flagged as duplicate:", dup_result)

        # 4. Resolve it by overwriting
        await ws.send(json.dumps({
            "type": "resolve_duplicate", "student_id": "3001", "action": "overwrite",
            "new_score_payload": {
                "mcq_score": 7.0, "essay_total": 2.0, "total_score": 9.0,
                "mistakes": [], "answer_version": "A", "group_type": "M",
            },
        }))
        resolve_result = json.loads(await ws.recv())
        assert resolve_result["status"] == "success"
        print("✔ Duplicate resolved via overwrite:", resolve_result)


def run_phone_simulation_then_quit(app, server):
    async def _wrapper():
        try:
            await simulate_phone()
            print("\n✔✔✔ Live grading test passed.")
        except Exception as e:
            print("\n✘✘✘ Live grading test FAILED:", e)
        finally:
            server.stop()
            server.wait()
            app.quit()

    asyncio.run(_wrapper())


def main():
    tmp_dir = tempfile.mkdtemp(prefix="nexus_live_test_")
    try:
        app = QCoreApplication(sys.argv)

        pm = ProjectManager()
        pm.create_project("Mock Live Exam", tmp_dir)
        pm.set_session_password("12345678")

        mock_packet = {"exam_name": "Mock Live Exam", "mcq_count": 10}  # minimal, enough to auth-check keys

        server = WebSocketServer(
            packet_data=mock_packet, db_path=pm.db_path,
            session_id=new_session_id(), group_name="Sidi Beshr",
            session_password="12345678",
        )
        server.log_signal.connect(lambda msg: print("[SERVER LOG]", msg))
        server.start()

        # Give the server a beat to bind the port before the "phone" connects.
        QTimer.singleShot(500, lambda: run_phone_simulation_then_quit(app, server))

        app.exec()

        # Verify what actually landed in the DB
        grades = GradingRepository.get_group_grades(pm.db_path, "Sidi Beshr")
        print("\nFinal grades in DB:", grades)
        assert len(grades) == 1, f"expected exactly 1 grade row after overwrite, got {len(grades)}"
        assert grades[0]["final_score"] == 9.0, "overwrite should have replaced 10.0 with 9.0"
        print("✔ DB state matches expected post-overwrite result.")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()