"""Small network helpers."""

from __future__ import annotations

import socket


def get_local_ips() -> list[str]:
    """Return the machine's LAN IPv4 addresses (loopback excluded)."""
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip in ips:
                continue
            ips.append(ip)
    except OSError:
        pass

    if not ips:
        # Fallback: a UDP "connect" reveals the default outbound interface
        # without sending any packet.
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                ips.append(s.getsockname()[0])
            finally:
                s.close()
        except OSError:
            ips.append("<your-ip-here>")

    return ips
