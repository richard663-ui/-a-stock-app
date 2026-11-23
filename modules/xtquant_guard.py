"""Keep xtquant from leaking a client on every failed connect while QMT is down.

Measured on 2026-11-23 with QMT logged out: every failed xtdata call blocked for
about 2 seconds and leaked 4.45 handles and 136 KB, because xtquant builds a new
native client for each attempt and never frees the failed one. The recorders call
xtdata several times a second, so while QMT stayed down (2026-11-14 to 11-23) each
service grew to ~640 MB and passed 20,000 handles, and its resource guard restarted
it every 2.7 hours.

The guard sits in front of ``xtdata.get_client``, which every xtdata function calls
first. While QMT is up nothing changes: a connected client is returned as before.
After a failed connect, further calls wait the same ~2 seconds a failed connect used
to take and then raise xtquant's own error, so the services keep their pace and log
volume, without building a client. A real connect is tried as soon as the QMT port
accepts a plain TCP connection (a socket that leaks nothing), and in any case after a
back-off that grows from 5 to 60 seconds.
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional

# xtquant's own message, so every existing handler and log reader keeps working.
DOWN_MESSAGE = "无法连接xtquant服务，请检查QMT-投研版或QMT-极简版是否开启"
DEFAULT_PORT = 58610
PROBE_TIMEOUT = 0.05   # a listening localhost port answers in well under a millisecond
PROBE_EVERY = 0.5      # seconds between port checks while a call waits
SKIP_DELAY = 2.0       # what a failed xtquant connect took; keeps the callers' pace
FIRST_WAIT = 5.0       # back-off before the second real connect ...
MAX_WAIT = 60.0        # ... doubling up to this


def candidate_ports(home: Optional[Path] = None) -> List[int]:
    """Ports xtquant itself tries: its default plus those in ~/.xtquant/*/xtdata.cfg."""
    ports = [DEFAULT_PORT]
    root = Path(home) if home is not None else Path(os.environ.get("USERPROFILE") or Path.home())
    try:
        entries = list((root / ".xtquant").iterdir())
    except OSError:
        return ports
    for entry in entries:
        try:
            config = json.loads((entry / "xtdata.cfg").read_text(encoding="utf-8"))
            port = int(config.get("port") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if 0 < port < 65536 and port not in ports:
            ports.append(port)
    return ports


def port_open(ports: Iterable[int], timeout: float = PROBE_TIMEOUT) -> bool:
    for port in ports:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
                return True
        except (OSError, ValueError):
            continue
    return False


def _print(message: str) -> None:
    print(message, flush=True)


class ConnectGuard:
    """Replacement for ``xtdata.get_client``; see the module docstring."""

    is_qmt_connect_guard = True

    def __init__(self, get_client: Callable[[], Any], current_client: Callable[[], Any],
                 probe: Callable[[], bool], clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = _print,
                 skip_delay: float = SKIP_DELAY, first_wait: float = FIRST_WAIT,
                 max_wait: float = MAX_WAIT, probe_every: float = PROBE_EVERY) -> None:
        self._get_client = get_client
        self._current_client = current_client
        self._probe = probe
        self._clock = clock
        self._sleep = sleep
        self._log = log
        self.skip_delay = skip_delay
        self.first_wait = first_wait
        self.max_wait = max_wait
        self.probe_every = probe_every
        self.failures = 0
        self.skipped = 0
        self.next_attempt = 0.0

    def _connected_client(self) -> Any:
        client = self._current_client()
        if client is None:
            return None
        try:
            return client if client.is_connected() else None
        except Exception:
            return None

    def _port_opens_within(self, seconds: float) -> bool:
        deadline = self._clock() + seconds
        while True:
            try:
                if self._probe():
                    return True
            except Exception:
                pass
            remaining = deadline - self._clock()
            if remaining <= 0:
                return False
            self._sleep(min(self.probe_every, remaining))

    def __call__(self) -> Any:
        client = self._connected_client()
        if client is not None:
            return client
        if self.failures and self._clock() < self.next_attempt:
            if not self._port_opens_within(self.skip_delay):
                self.skipped += 1
                raise Exception(DOWN_MESSAGE)
        try:
            client = self._get_client()
        except Exception:
            self.failures += 1
            wait = min(self.max_wait, self.first_wait * 2 ** min(self.failures - 1, 16))
            self.next_attempt = self._clock() + wait
            if self.failures == 1:
                self._log("[QMT_GUARD] QMT unreachable: xtdata calls now wait and fail without "
                          f"connecting; next connect when its port opens, at most {self.max_wait:.0f}s apart.")
            raise
        if self.failures:
            self._log(f"[QMT_GUARD] QMT reachable again after {self.failures} failed connect(s); "
                      f"{self.skipped} call(s) answered without connecting.")
        self.failures = 0
        self.skipped = 0
        self.next_attempt = 0.0
        return client


def install(xtdata: Any, log: Callable[[str], None] = _print,
            probe: Optional[Callable[[], bool]] = None) -> Optional[ConnectGuard]:
    """Put the guard in front of ``xtdata.get_client``. Safe to call more than once."""
    current = getattr(xtdata, "get_client", None)
    if getattr(current, "is_qmt_connect_guard", False):
        return current
    if not callable(current):
        return None
    namespace = vars(xtdata)
    guard = ConnectGuard(get_client=current,
                         current_client=lambda: namespace.get("__client"),
                         probe=probe or (lambda: port_open(candidate_ports())),
                         log=log)
    xtdata.get_client = guard
    return guard
