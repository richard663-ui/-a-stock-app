"""Keep xtquant from leaking a client on every failed connect while QMT is down.

Measured on 2026-11-23 with QMT logged out: every failed xtdata call blocked for
about 2 seconds and leaked 4.45 handles, 136 KB and one bound TCP port, because
xtquant builds a new native client for each attempt and never frees the failed one.
The recorders call xtdata several times a second. While QMT stayed down (2026-11-14
to 11-23) each service passed 20,000 handles every few hours and its resource guard
restarted it, until on 11-22/23 all three hung at about 3,840 leaked clients each
(~16,000 handles; 11,500 ports held between them) and could not even be killed:
only a restart of the PC frees them.

The guard sits in front of ``xtdata.get_client``, which every xtdata function calls
first. While QMT is up nothing changes: a connected client is returned as before.
Otherwise a real connect is made only when an address xtquant would try accepts a
plain TCP connection (a socket closed at once, which leaks nothing). While nothing
listens, a call waits the ~2 seconds a failed connect used to take and raises
xtquant's own error, so the services keep their pace and log volume. If a real
connect still fails on an open port, the next one waits a back-off growing from 5
to 60 seconds.

xtquant 250807 tries ``xtconn.scan_available_server_addr()`` (the ports in
``~/.xtquant/*/xtdata.cfg`` of running instances, plus an in-process data centre,
which no service here starts) and then 127.0.0.1:58610. ``candidate_addresses``
covers all of these; re-check it when xtquant is upgraded.
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Tuple

# xtquant's own message, so every existing handler and log reader keeps working.
DOWN_MESSAGE = "无法连接xtquant服务，请检查QMT-投研版或QMT-极简版是否开启"
LOCALHOST = "127.0.0.1"
DEFAULT_PORT = 58610
PROBE_TIMEOUT = 0.05   # a listening localhost port answers in well under a millisecond
REMOTE_TIMEOUT = 1.0   # for a configured non-local address
PROBE_EVERY = 0.5      # seconds between port checks while a call waits
SKIP_DELAY = 2.0       # what a failed xtquant connect took; keeps the callers' pace
FIRST_WAIT = 5.0       # after a failed real connect, back off this long ...
MAX_WAIT = 60.0        # ... doubling up to this

_LOCAL_NAMES = {"", "0.0.0.0", "localhost", LOCALHOST}

Address = Tuple[str, int]


def candidate_addresses(home: Optional[Path] = None) -> List[Address]:
    """Every address xtquant may try: its default plus each ~/.xtquant/*/xtdata.cfg."""
    found: List[Address] = [(LOCALHOST, DEFAULT_PORT)]
    root = Path(home) if home is not None else Path(os.environ.get("USERPROFILE") or Path.home())
    try:
        entries = list((root / ".xtquant").iterdir())
    except OSError:
        return found
    for entry in entries:
        try:
            config = json.loads((entry / "xtdata.cfg").read_text(encoding="utf-8"))
            port = int(config.get("port") or 0)
            ip = str(config.get("ip") or "").strip()
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if ip in _LOCAL_NAMES:
            ip = LOCALHOST
        if 0 < port < 65536 and (ip, port) not in found:
            found.append((ip, port))
    return found


def address_open(addresses: Iterable[Address], timeout: float = PROBE_TIMEOUT) -> bool:
    for ip, port in addresses:
        wait = timeout if ip == LOCALHOST else max(timeout, REMOTE_TIMEOUT)
        try:
            with socket.create_connection((ip, int(port)), timeout=wait):
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
        self.down = False        # QMT unreachable since the last successful connect
        self.failures = 0        # real connects that failed since then
        self.skipped = 0         # calls answered without connecting since then
        self.next_attempt = 0.0  # no real connect before this, after a failed one

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

    def _mark_down(self) -> None:
        if not self.down:
            self.down = True
            self._log("[QMT_GUARD] QMT unreachable: xtdata calls now wait and fail without "
                      "connecting; reconnect as soon as its port opens.")

    def _fail_without_connecting(self) -> None:
        self._mark_down()
        self.skipped += 1
        raise Exception(DOWN_MESSAGE)

    def __call__(self) -> Any:
        client = self._connected_client()
        if client is not None:
            return client
        if self.failures and self._clock() < self.next_attempt:
            self._sleep(self.skip_delay)          # an open port refused us: wait out the back-off
            self._fail_without_connecting()
        if not self._port_opens_within(self.skip_delay):
            self._fail_without_connecting()       # nothing listens: a connect could only leak
        try:
            client = self._get_client()
        except Exception:
            self.failures += 1
            wait = min(self.max_wait, self.first_wait * 2 ** min(self.failures - 1, 16))
            self.next_attempt = self._clock() + wait
            self._mark_down()
            raise
        if self.down:
            self._log(f"[QMT_GUARD] QMT reachable again after {self.failures} failed connect(s); "
                      f"{self.skipped} call(s) answered without connecting.")
        self.down = False
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
                         probe=probe or (lambda: address_open(candidate_addresses())),
                         log=log)
    xtdata.get_client = guard
    return guard
