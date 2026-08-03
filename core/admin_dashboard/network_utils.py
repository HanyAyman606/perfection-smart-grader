"""
network_utils.py
-------------------
Best-effort LAN IP detection so the sidebar can show admins the address
mobiles should connect to, without them having to open a terminal.

Uses the "connect a UDP socket, read its local address" trick — this
never actually sends a packet (UDP connect() is just a routing-table
lookup), so it's safe to call even with no real network activity.
"""

import socket


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()