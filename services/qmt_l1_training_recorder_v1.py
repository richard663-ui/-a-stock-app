# -*- coding: utf-8 -*-
"""L1/Tick-only 60s ML recorder.

Uses the existing QMT tick/L1 capture, five-second samples, opening-auction tags,
market context and +60s smoothed-mid labels, but deliberately performs NO
Level-2 subscription. This is the clean baseline used before paying for L2.
"""
from __future__ import annotations

from typing import Any, Dict
import time
from xtquant import xtdata

import services.qmt_l2_training_recorder_v6 as v6
from modules.level2_engine import analyze_level2

base = v6.base
RECORDER_VERSION = "l1-training-recorder-v2-shanghai-tick-20261010"
base.RECORDER_VERSION = RECORDER_VERSION

_PERIODS = (
    "l2quote", "l2transaction", "l2order", "l2quoteaux",
    "l2transactioncount", "l2orderqueue",
)


class L1OnlyManager:
    """Subscribe to L1 ticks while reporting all Level-2 capabilities unavailable.

    Keeping the same interface lets the mature recorder/label pipeline run
    unchanged while guaranteeing that no broker/external L2 call can block it.
    """

    def __init__(self) -> None:
        self.symbol = ""
        self.subscription_id = None
        self.subscription_error = ""
        self.last_attempt = 0.0

    @property
    def available_runtime(self) -> bool:
        return True

    def switch(self, symbol: str) -> Dict[str, Any]:
        self.stop()
        self.symbol = str(symbol or "").upper().strip()
        self._subscribe()
        return self.status()

    def stop(self) -> None:
        if self.subscription_id is not None:
            try:
                xtdata.unsubscribe_quote(self.subscription_id)
            except Exception:
                pass
        self.subscription_id = None

    def _subscribe(self) -> None:
        if not self.symbol or self.subscription_id is not None:
            return
        if self.last_attempt and time.monotonic() - self.last_attempt < 10:
            return
        self.last_attempt = time.monotonic()
        try:
            subscription_id = xtdata.subscribe_quote(self.symbol, period="tick", count=0)
            if subscription_id is None or int(subscription_id) < 0:
                raise RuntimeError("QMT tick subscription rejected")
            self.subscription_id = int(subscription_id)
            self.subscription_error = ""
        except Exception as exc:
            self.subscription_error = str(exc)

    def refresh(self, force: bool = False) -> Dict[str, int]:
        if force and time.monotonic() - self.last_attempt >= 10:
            self.stop()
        self._subscribe()
        return {p: 0 for p in _PERIODS}

    def status(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "runtime_available": True,
            "runtime_error": self.subscription_error,
            "tick_subscription_id": self.subscription_id,
            "capabilities": {
                p: {"available": False, "subscription_id": None, "error": "L1_BASELINE", "source": "l1_baseline"}
                for p in _PERIODS
            },
            "counts": {p: 0 for p in _PERIODS},
            "available_count": 0,
            "core_ready": False,
        }

    def snapshot(self) -> Dict[str, Any]:
        self._subscribe()
        summary = analyze_level2(
            quotes=[], transactions=[], orders=[], quoteaux=[],
            transactioncount=[], orderqueue=[], window_seconds=60,
        )
        return {
            **self.status(),
            "summary": summary,
            "recent_transactions": [],
            "recent_orders": [],
            "quoteaux": {},
            "orderqueue": {},
            "age_seconds": {p: None for p in _PERIODS},
        }


base.QMTLevel2Manager = L1OnlyManager

_base_write_status = base._write_status


def _write_status(payload: Dict[str, Any]) -> None:
    out = dict(payload)
    out["recorder_version"] = RECORDER_VERSION
    out["l2_provider"] = "l1_baseline"
    out["ml_data_mode"] = "L1_BASELINE"
    _base_write_status(out)


base._write_status = _write_status


def main() -> None:
    print("AStock L1/Tick 60s training recorder started")
    print(f"Recorder: {RECORDER_VERSION}")
    print("Mode: L1_BASELINE - no Level-2 subscription is attempted.")
    print("5s state samples + +60s smoothed-mid labels remain unchanged.")
    print("09:15-09:25 auction stays separate; 09:30-10:30 remains the priority regime.")
    base.main()


if __name__ == "__main__":
    main()
