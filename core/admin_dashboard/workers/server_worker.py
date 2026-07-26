"""
workers/server_worker.py
------------------------
Background thread that listens for a mobile client and pushes the exam
sync packet (blueprint + roster) to it once connected.
"""

import json
import socket

from PySide6.QtCore import QThread, Signal

SYNC_PORT = 8765


class ServerWorker(QThread):
    log_signal = Signal(str)

    def __init__(self, packet_data: dict):
        super().__init__()
        self.packet_data = packet_data

    def run(self):
        try:
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(("0.0.0.0", SYNC_PORT))
            server_socket.listen(5)

            self.log_signal.emit(f"SERVER ONLINE: Listening on port {SYNC_PORT}...")

            while True:
                client_sock, addr = server_socket.accept()
                self.log_signal.emit(f"CONNECTION DETECTED: Mobile Client connected from {addr[0]}")

                payload = json.dumps(self.packet_data).encode("utf-8")
                client_sock.sendall(payload + b"\n")
                client_sock.close()
                self.log_signal.emit("PACKET TRANSMITTED: Exam blueprint & roster synced to client.")

        except Exception as e:
            self.log_signal.emit(f"Server Error: {str(e)}")
