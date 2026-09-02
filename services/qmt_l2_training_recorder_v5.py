# -*- coding: utf-8 -*-
"""V5 L2 training recorder: efficient V4 + market context + L2 freshness.

This is still research-only. It keeps the stable eight-stock basket, 5-second
training samples and +60s smoothed-mid labels. It adds two lightweight L1 market
benchmarks and explicit L2 age features so stale/market-wide moves are not hidden
inside stock-specific predictors.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List

from xtquant import xtdata

import services.qmt_l2_training_recorder_v4 as v4
from modules.qmt_level2 import QMTLevel2Manager as _BaseL2Manager

base = v4.base
RECORDER_VERSION = "l2-training-recorder-v5-market-freshness-20260902"
BENCHMARKS = ("000300.SH", "399006.SZ")
MARKET_CACHE_SECONDS = 1.0
L2_FRESH_SECONDS = 5.0

base.RECORDER_VERSION = RECORDER_VERSION


class FreshQMTLevel2Manager(_BaseL2Manager):
    def __init__(self) -> None:
        super().__init__()
        self._last_update_ts: Dict[str, float] = {p: 0.0 for p in self.buffers}

    def _append(self, period: str, rows: Iterable[Dict[str, Any]]) -> None:
        materialized: List[Dict[str, Any]] = [dict(x) for x in rows if isinstance(x, dict) and x]
        if materialized:
            self._last_update_ts[period] = time.time()
        super()._append(period, materialized)

    def switch(self, symbol: str) -> Dict[str, Any]:
        self._last_update_ts = {p: 0.0 for p in self.buffers}
        return super().switch(symbol)

    def snapshot(self) -> Dict[str, Any]:
        snap = super().snapshot()
        now = time.time()
        snap["age_seconds"] = {
            p: (max(0.0, now - ts) if ts > 0 else None)
            for p, ts in self._last_update_ts.items()
        }
        return snap


base.QMTLevel2Manager = FreshQMTLevel2Manager

_market_cache: Dict[str, float] = {}
_market_cache_ts = 0.0


def _pct_from_tick(tick: Dict[str, Any]) -> float:
    try:
        last = float(tick.get("lastPrice") or tick.get("price") or 0.0)
        close = float(tick.get("lastClose") or tick.get("preClose") or 0.0)
        return (last / close - 1.0) * 100.0 if last > 0 and close > 0 else 0.0
    except Exception:
        return 0.0


def _market_features() -> Dict[str, float]:
    global _market_cache, _market_cache_ts
    now = time.time()
    if now - _market_cache_ts <= MARKET_CACHE_SECONDS and _market_cache:
        return dict(_market_cache)
    try:
        raw = xtdata.get_full_tick(list(BENCHMARKS)) or {}
        hs300 = _pct_from_tick(raw.get("000300.SH") or {})
        chinext = _pct_from_tick(raw.get("399006.SZ") or {})
        _market_cache = {
            "market_hs300_return_pct": hs300,
            "market_chinext_return_pct": chinext,
        }
        _market_cache_ts = now
    except Exception:
        if not _market_cache:
            _market_cache = {"market_hs300_return_pct": 0.0, "market_chinext_return_pct": 0.0}
    return dict(_market_cache)


_base_feature_snapshot = base._feature_snapshot


def _feature_snapshot(row, tick_rows, snap):
    features, meta = _base_feature_snapshot(row, tick_rows, snap)
    ages = dict(snap.get("age_seconds") or {})

    def age(name: str) -> float:
        value = ages.get(name)
        try:
            return max(0.0, float(value)) if value is not None else 999.0
        except Exception:
            return 999.0

    q_age = age("l2quote")
    t_age = age("l2transaction")
    o_age = age("l2order")
    oq_age = age("l2orderqueue")
    core_ages = [x for x in (t_age, o_age) if x < 999.0]
    core_max = max(core_ages) if core_ages else 999.0
    core_fresh = bool(core_ages and core_max <= L2_FRESH_SECONDS)

    features.update({
        "l2_quote_age_s": q_age,
        "l2_transaction_age_s": t_age,
        "l2_order_age_s": o_age,
        "l2_orderqueue_age_s": oq_age,
        "l2_core_max_age_s": core_max,
        "l2_core_fresh": int(core_fresh),
    })
    market = _market_features()
    features.update(market)
    stock_ret = float(features.get("day_return_pct") or 0.0)
    features["relative_to_hs300_pct"] = stock_ret - float(market.get("market_hs300_return_pct") or 0.0)
    features["relative_to_chinext_pct"] = stock_ret - float(market.get("market_chinext_return_pct") or 0.0)

    # Training must not call cached or aged transaction/order state "true L2".
    meta["true_l2"] = bool(meta.get("true_l2") and core_fresh)
    return features, meta


base._feature_snapshot = _feature_snapshot


def main() -> None:
    print("AStock L2 training recorder V5 market/freshness mode")
    print(f"Recorder: {RECORDER_VERSION}")
    print("5s samples and +60s smoothed-mid labels unchanged.")
    print("Adds HS300/ChiNext relative context and explicit L2 age; stale core L2 is not trainable.")
    base.main()


if __name__ == "__main__":
    main()
