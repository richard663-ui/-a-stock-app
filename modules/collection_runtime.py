"""Keep an AC-powered recorder awake and reject windows spanning interruptions."""
from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass


class WindowsPowerBackend:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
        self.api.SetThreadExecutionState.restype = ctypes.c_uint32

    def ac_connected(self):
        # SYSTEM_POWER_STATUS contains four BYTEs followed by two DWORDs.
        data = (ctypes.c_ubyte * 12)()
        if not self.api.GetSystemPowerStatus(ctypes.byref(data)):
            raise OSError("GetSystemPowerStatus failed")
        return data[0] == 1

    def set_required(self, required):
        flags = 0x80000000 | (0x00000001 if required else 0)
        if not self.api.SetThreadExecutionState(flags):
            raise OSError("SetThreadExecutionState failed")


class RecorderPowerGuard:
    """Call update/close from one recorder thread; never alter global power plans."""
    def __init__(self, backend=None, enabled=None):
        self.enabled = (os.environ.get("ASTOCK_KEEP_AWAKE", "1") != "0") if enabled is None else enabled
        self.active = False
        self.error = ""
        self.reason = "disabled" if not self.enabled else "unsupported_platform"
        self.backend = backend
        if self.enabled and backend is None and sys.platform == "win32":
            try:
                self.backend = WindowsPowerBackend()
            except OSError as exc:
                self.error = str(exc)

    def update(self):
        if not self.enabled or self.backend is None:
            return self.status()
        try:
            required = self.backend.ac_connected()
            if required != self.active:
                self.backend.set_required(required)
                self.active = required
            self.reason = "recorder_running_on_ac" if required else "battery_or_unknown_power"
            self.error = ""
        except OSError as exc:
            self.error = str(exc)
            self.reason = "power_request_failed"
            if self.active:
                try:
                    self.backend.set_required(False)
                    self.active = False
                except OSError:
                    pass
        return self.status()

    def close(self):
        if self.backend is not None and self.active:
            try:
                self.backend.set_required(False)
                self.active = False
            except OSError as exc:
                self.error = str(exc)

    def status(self):
        return {"enabled": self.enabled, "active": self.active, "reason": self.reason,
                "error": self.error, "display_required": False,
                "manual_sleep_prevented": False, "global_power_plan_changed": False}


@dataclass
class CollectionContinuity:
    max_gap_seconds: float = 12.0
    last_loop_at: float | None = None
    loop_gaps: int = 0
    feed_gaps: int = 0
    largest_loop_gap_seconds: float = 0.0
    last_gap_at: float | None = None

    def __post_init__(self):
        self.last_exchange = {}

    def observe_loop(self, timestamp):
        gap = 0.0 if self.last_loop_at is None else timestamp - self.last_loop_at
        self.last_loop_at = timestamp
        interrupted = gap > self.max_gap_seconds or gap < 0
        if interrupted:
            self.loop_gaps += 1
            self.largest_loop_gap_seconds = max(self.largest_loop_gap_seconds, gap)
            self.last_gap_at = timestamp
            self.last_exchange.clear()
        return interrupted

    def observe_tick(self, symbol, timestamp):
        previous = self.last_exchange.get(symbol)
        self.last_exchange[symbol] = timestamp
        interrupted = previous is not None and (
            timestamp - previous > self.max_gap_seconds or timestamp < previous
        )
        if interrupted:
            self.feed_gaps += 1
            self.last_gap_at = timestamp
        return interrupted

    def status(self):
        return {"loop_gap_count": self.loop_gaps, "feed_gap_count": self.feed_gaps,
                "largest_loop_gap_seconds": round(self.largest_loop_gap_seconds, 1),
                "last_gap_epoch": self.last_gap_at, "max_gap_seconds": self.max_gap_seconds}
