# -*- coding: utf-8 -*-
"""Small single-instance guard for the local bridge."""
from __future__ import annotations

import socket
from typing import Optional


def acquire_bridge_lock(port: int = 49327) -> Optional[socket.socket]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind(("127.0.0.1", int(port)))
        sock.listen(1)
        return sock
    except OSError:
        try:
            sock.close()
        except Exception:
            pass
        return None
