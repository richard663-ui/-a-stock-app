"""Exit a long-running service cleanly before a leak can exhaust the PC.

On 2026-10-29/30 services waiting for QMT leaked up to 9 GB and 265,000 handles
each. A daemon thread checks this process's private memory and handle count;
over a limit it exits with RESTART_EXIT_CODE so the watchdog starts a fresh one.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable, Dict, Optional

RESTART_EXIT_CODE = 75
DEFAULT_MAX_MEMORY_MB = 4096
DEFAULT_MAX_HANDLES = 20000


class _Counters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


def usage() -> Optional[Dict[str, float]]:
    """Private memory (MB) and handle count of this process; None off Windows."""
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    process = kernel32.GetCurrentProcess()
    counters = _Counters()
    counters.cb = ctypes.sizeof(_Counters)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        return None
    handles = wintypes.DWORD()
    kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessHandleCount(process, ctypes.byref(handles))
    return {"memory_mb": counters.PrivateUsage / 1e6, "handles": float(handles.value)}


def over_limit(sample: Optional[Dict[str, float]], max_memory_mb: float, max_handles: float) -> str:
    if not sample:
        return ""
    if sample["memory_mb"] > max_memory_mb:
        return f"memory {sample['memory_mb']:.0f} MB > {max_memory_mb:.0f} MB"
    if sample["handles"] > max_handles:
        return f"handles {sample['handles']:.0f} > {max_handles:.0f}"
    return ""


def start_guard(name: str, max_memory_mb: Optional[float] = None, max_handles: Optional[float] = None,
                interval: float = 30.0, sampler: Callable = usage,
                on_exceed: Optional[Callable[[str], None]] = None) -> threading.Thread:
    """Start the watchdog thread. Limits can be overridden with ASTOCK_MAX_MEMORY_MB / ASTOCK_MAX_HANDLES."""
    memory = float(os.environ.get("ASTOCK_MAX_MEMORY_MB") or max_memory_mb or DEFAULT_MAX_MEMORY_MB)
    handles = float(os.environ.get("ASTOCK_MAX_HANDLES") or max_handles or DEFAULT_MAX_HANDLES)

    def _exit(reason: str) -> None:
        print(f"[RESOURCE_GUARD] {name}: {reason}; exiting {RESTART_EXIT_CODE} for a clean restart.", flush=True)
        os._exit(RESTART_EXIT_CODE)

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                reason = over_limit(sampler(), memory, handles)
            except Exception:
                reason = ""
            if reason:
                (on_exceed or _exit)(reason)
                return

    thread = threading.Thread(target=_loop, daemon=True, name="AStockResourceGuard")
    thread.start()
    return thread
