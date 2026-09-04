# -*- coding: utf-8 -*-
"""V7 research recorder: non-blocking MACD context on top of V6 integrity.

V6 made expiry evaluation first, but it still called the multi-timeframe Tencent
MACD fetch synchronously from the timing loop. One refresh can issue six HTTP
requests per symbol, so eight symbols can stall the wall-clock evaluator by many
seconds. V7 keeps V5B prediction rules unchanged while making MACD context a
best-effort background cache. The timing loop never waits for network MACD.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

import services.qmt_research_recorder_v6 as v6
from modules.macd_calibration_v5 import get_context

RECORDER_VERSION = "research-recorder-v7-nonblocking-macd-20260904"
v6.RECORDER_VERSION = RECORDER_VERSION
v6.base.RECORDER_VERSION = RECORDER_VERSION

_MACD_REFRESH_SECONDS = 30.0
_macd_lock = threading.Lock()
_macd_cache: Dict[str, Dict[str, Any]] = {}
_macd_requested: Dict[str, float] = {}
_macd_inflight = set()
_macd_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="research-macd")


def _refresh(symbol: str) -> None:
    try:
        ctx = dict(get_context(symbol) or {})
        ctx["async_cached_at"] = time.time()
        with _macd_lock:
            _macd_cache[symbol] = ctx
    finally:
        with _macd_lock:
            _macd_inflight.discard(symbol)


def _nonblocking_macd(self, symbol: str) -> Dict[str, Any]:
    now = time.time()
    with _macd_lock:
        cached = dict(_macd_cache.get(symbol) or {})
        last = float(_macd_requested.get(symbol) or 0.0)
        if symbol not in _macd_inflight and now - last >= _MACD_REFRESH_SECONDS:
            _macd_inflight.add(symbol)
            _macd_requested[symbol] = now
            _macd_pool.submit(_refresh, symbol)
    if cached:
        cached["nonblocking"] = True
        return cached
    return {
        "symbol": symbol,
        "score": 0,
        "bias_score": 0.0,
        "summary": "MACD后台刷新中",
        "resonance": "--",
        "timeframes": {},
        "fetched_ts": 0.0,
        "calibration_version": "macd-structure-v9",
        "nonblocking": True,
    }


# v6.main resolves cloud.macd_bias dynamically, so this patch removes all HTTP
# waiting from its 0.2-second expiry/tick loop without touching V5B weights.
v6.base.CloudState.macd_bias = _nonblocking_macd


def main() -> None:
    print("AStock Research Recorder V7 started")
    print(f"Recorder: {RECORDER_VERSION}")
    print("V5B predictive rules unchanged; MACD HTTP refresh is background-only.")
    print("60s/120s expiry timing never waits for Tencent network requests.")
    v6.main()


if __name__ == "__main__":
    main()
