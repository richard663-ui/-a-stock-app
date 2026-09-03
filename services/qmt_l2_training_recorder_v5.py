# -*- coding: utf-8 -*-
"""V5 L2 training recorder: efficient V4 + market context + L2 freshness.

Research-only. Keeps the stable basket, 5-second samples and +60s smoothed-mid
labels, while adding lightweight market context and explicit L2 freshness.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List

from xtquant import xtdata

import services.qmt_l2_training_recorder_v4 as v4
from modules.qmt_level2 import QMTLevel2Manager as _BaseL2Manager

base = v4.base
RECORDER_VERSION = "l2-training-recorder-v5-market-freshness-20260903b"
BENCHMARKS = ("000300.SH", "399006.SZ")
MARKET_CACHE_SECONDS = 1.0
L2_FRESH_SECONDS = 5.0

base.RECORDER_VERSION = RECORDER_VERSION


class FreshQMTLevel2Manager(_BaseL2Manager):
    def __init__(self) -> None:
        super().__init__()
        self._last_update_ts: Dict[str, float] = {p: 0.0 for p in self.buffers}

    def _append(self, period: str, rows: Iterable[Dict[str, Any]], source: str = "callback") -> int:
        """Track freshness only when genuinely NEW rows were appended.

        QMT cache polling can return the same tail repeatedly. Re-seeing cached
        rows must never refresh the feed timestamp, otherwise a frozen Level-2
        feed could be mislabeled as fresh forever.
        """
        materialized: List[Dict[str, Any]] = [dict(x) for x in rows if isinstance(x, dict) and x]
        added = int(super()._append(period, materialized, source=source) or 0)
        if added:
            self._last_update_ts[period] = time.time()
        return added

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
    caps = dict(snap.get("capabilities") or {})

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

    # Event age measures activity, not just connection health. A quiet stock can
    # legitimately have no new transaction/order for a few seconds while quote
    # snapshots are still fresh, so feed freshness uses the freshest core stream.
    available_core_ages = [
        x for x in (q_age, t_age, o_age)
        if x < 999.0
    ]
    feed_age = min(available_core_ages) if available_core_ages else 999.0
    feed_fresh = bool(available_core_ages and feed_age <= L2_FRESH_SECONDS)
    event_ages = [x for x in (t_age, o_age) if x < 999.0]
    core_max = max(event_ages) if event_ages else 999.0

    tx_available = bool((caps.get("l2transaction") or {}).get("available"))
    order_available = bool((caps.get("l2order") or {}).get("available"))

    features.update({
        "l2_quote_age_s": q_age,
        "l2_transaction_age_s": t_age,
        "l2_order_age_s": o_age,
        "l2_orderqueue_age_s": oq_age,
        "l2_core_max_age_s": core_max,
        "l2_core_fresh": int(feed_fresh),
        "l2_feed_age_s": feed_age,
        "l2_feed_fresh": int(feed_fresh),
        "l2_transaction_stream_seen": int(tx_available),
        "l2_order_stream_seen": int(order_available),
    })
    market = _market_features()
    features.update(market)
    stock_ret = float(features.get("day_return_pct") or 0.0)
    features["relative_to_hs300_pct"] = stock_ret - float(market.get("market_hs300_return_pct") or 0.0)
    features["relative_to_chinext_pct"] = stock_ret - float(market.get("market_chinext_return_pct") or 0.0)

    # A trainable true-L2 row requires that the transaction stream has actually
    # been observed and that at least one core L2 stream has genuinely updated
    # recently. Repeated old cache tails no longer satisfy this condition.
    meta["true_l2"] = bool(tx_available and feed_fresh)
    return features, meta


base._feature_snapshot = _feature_snapshot


def main() -> None:
    print("AStock L2 training recorder V5 market/freshness mode")
    print(f"Recorder: {RECORDER_VERSION}")
    print("5s samples and +60s smoothed-mid labels unchanged.")
    print("Freshness advances only on genuinely new L2 rows; repeated cache tails cannot fake a live feed.")
    base.main()


if __name__ == "__main__":
    main()
