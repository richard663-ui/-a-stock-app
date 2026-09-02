# -*- coding: utf-8 -*-
"""V3 launcher for the L2 training recorder with freshness and autonomous basket guards."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import services.qmt_l2_training_recorder_v2 as base
from modules.qmt_live import normalize_tick as _normalize_tick

RECORDER_VERSION = "l2-training-recorder-v3-freshness-20260901"
MAX_LIVE_TICK_AGE_SECONDS = 5.0

# Stable eight-name training basket. Keeping the same auxiliaries for several
# sessions is statistically more useful than random rotation because every name
# accumulates enough forward labels for out-of-sample comparison.
BACKGROUND_TRAINING_SYMBOLS = (
    "301236.SZ",
    "300308.SZ",
    "000400.SZ",
    "600522.SH",
    "601179.SH",
    "600105.SH",
    "002916.SZ",
    "000811.SZ",
)

_base_load_watchlist = base._load_watchlist


def _background_watchlist():
    """Keep the fixed basket first; append user names only if spare slots exist."""
    symbols = list(BACKGROUND_TRAINING_SYMBOLS)
    try:
        extra = list(_base_load_watchlist())
    except Exception:
        extra = []
    for symbol in extra:
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols[: base.MAX_SYMBOLS]


def _fresh_normalize(symbol: str, tick: dict, fallback_time: Any = None) -> dict:
    row = _normalize_tick(symbol, tick, fallback_time=fallback_time)
    text = str(row.get("captured_at") or "")
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return {}
    # QMT can keep returning its last cached tick after a service/network break.
    # During market hours that must never be turned into repeated flat labels.
    if base._market_open() and abs(time.time() - ts) > MAX_LIVE_TICK_AGE_SECONDS:
        return {}
    return row


base._load_watchlist = _background_watchlist
base.normalize_tick = _fresh_normalize
base.RECORDER_VERSION = RECORDER_VERSION


def main() -> None:
    print(f"Freshness guard: reject market-hour ticks older than {MAX_LIVE_TICK_AGE_SECONDS:.0f}s")
    print("Autonomous L2 basket: " + ", ".join(BACKGROUND_TRAINING_SYMBOLS))
    print("Phone switching is not required for training data collection.")
    base.main()


if __name__ == "__main__":
    main()
