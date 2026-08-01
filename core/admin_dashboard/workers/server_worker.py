"""
workers/server_worker.py
------------------------
Background thread that listens for a mobile client and pushes the exam
sync packet (blueprint) to it once connected.
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
        self._server_socket = None
        self._stop_requested = False

    def run(self):
        try:
            self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_socket.bind(("0.0.0.0", SYNC_PORT))
            self._server_socket.listen(5)

            self.log_signal.emit(f"SERVER ONLINE: Listening on port {SYNC_PORT}...")

            while not self._stop_requested:
                try:
                    client_sock, addr = self._server_socket.accept()
                except OSError:
                    # stop() closed the listening socket to unblock us — exit quietly.
                    break

                self.log_signal.emit(f"CONNECTION DETECTED: Mobile Client connected from {addr[0]}")

                payload = json.dumps(self.packet_data).encode("utf-8")
                client_sock.sendall(payload + b"\n")
                client_sock.close()
                self.log_signal.emit("PACKET TRANSMITTED: Exam blueprint synced to client.")

            self.log_signal.emit("SERVER OFFLINE.")

        except Exception as e:
            self.log_signal.emit(f"Server Error: {str(e)}")
        finally:
            if self._server_socket:
                try:
                    self._server_socket.close()
                except OSError:
                    pass

    def stop(self):
        """Call from the GUI thread. Closing the listening socket is what
        actually unblocks accept() — the flag alone can't interrupt a
        blocking syscall."""
        self._stop_requested = True
        if self._server_socket:
            try:
                self._server_socket.close()
            except OSError:
                pass
