# -*- coding: utf-8 -*-
"""V4 launcher/patch layer for the stable V3 recorder infrastructure.

Keeps V3's proven non-overlap, restart recovery and durable cloud sync, while
switching scoring to V4 and adding local intraday volume-at-price collection.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Dict

import services.qmt_research_recorder_v3 as base
import modules.research_forward_model_v4 as model

RECORDER_VERSION = "research-recorder-v4-20260826"
PRODUCTION_MODEL = "mobile-grouped-v8-stable-exhaustion"

base.RECORDER_VERSION = RECORDER_VERSION
base.MODEL_VERSION = model.MODEL_VERSION
base.PRODUCTION_MODEL = PRODUCTION_MODEL
base.score_rows = model.score_rows
base.score_label = model.score_label
base.high_confidence = model.high_confidence

_profile_hist: Dict[str, Dict[float, float]] = {}
_profile_last_volume: Dict[str, float] = {}
_profile_last_write: Dict[str, float] = {}
_profile_day = ""
_original_write_tick = base.DailyStore.write_tick


def _profile_snapshot(symbol: str):
    h = _profile_hist.get(symbol, {})
    if not h:
        return None
    total = sum(h.values())
    if total <= 0:
        return None
    items = sorted(h.items())
    poc_price, poc_vol = max(items, key=lambda kv: kv[1])
    ranked = sorted(items, key=lambda kv: kv[1], reverse=True)
    chosen = []
    acc = 0.0
    for item in ranked:
        chosen.append(item)
        acc += item[1]
        if acc / total >= 0.70:
            break
    prices = [p for p, _ in chosen]
    return {
        "poc_price": poc_price,
        "value_area_low": min(prices),
        "value_area_high": max(prices),
        "total_volume": total,
        "concentration": poc_vol / total,
        "profile": [[p, v] for p, v in sorted(ranked[:80], key=lambda kv: kv[0])],
    }


def _write_profile(store, symbol: str, now_ts: float):
    snap = _profile_snapshot(symbol)
    if not snap:
        return
    conn = store.ensure()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS volume_profile_v4 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            poc_price REAL,
            value_area_low REAL,
            value_area_high REAL,
            total_volume REAL,
            concentration REAL,
            profile_json TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'INTRADAY_VOLUME_AT_PRICE',
            UNIQUE(symbol,generated_at)
        )
    """)
    generated = datetime.fromtimestamp(now_ts).astimezone().replace(second=0, microsecond=0).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO volume_profile_v4(
            symbol,generated_at,poc_price,value_area_low,value_area_high,total_volume,concentration,profile_json
        ) VALUES (?,?,?,?,?,?,?,?)
    """, (
        symbol, generated, snap["poc_price"], snap["value_area_low"], snap["value_area_high"],
        snap["total_volume"], snap["concentration"], json.dumps(snap["profile"], separators=(",", ":")),
    ))
    conn.commit()


def _write_tick_v4(self, row):
    global _profile_day
    inserted = _original_write_tick(self, row)
    day = datetime.now().strftime("%Y-%m-%d")
    if day != _profile_day:
        _profile_day = day
        _profile_hist.clear(); _profile_last_volume.clear(); _profile_last_write.clear()

    symbol = str(row.get("symbol") or "")
    try:
        price = float(row.get("lastPrice") or 0.0)
        volume = float(row.get("volume") or 0.0)
    except Exception:
        return inserted
    if not symbol or price <= 0 or volume < 0:
        return inserted

    prev = _profile_last_volume.get(symbol)
    _profile_last_volume[symbol] = volume
    if prev is not None:
        dv = volume - prev
        if dv < 0:
            _profile_hist[symbol] = {}
        elif dv > 0:
            p = round(price + 1e-9, 2)
            h = _profile_hist.setdefault(symbol, {})
            h[p] = h.get(p, 0.0) + dv

    now_ts = time.time()
    if now_ts - _profile_last_write.get(symbol, 0.0) >= 60.0:
        try:
            _write_profile(self, symbol, now_ts)
            _profile_last_write[symbol] = now_ts
        except Exception as exc:
            print(f"[WARN] volume profile write failed: {exc}")
    return inserted


base.DailyStore.write_tick = _write_tick_v4


def main():
    print("V4 patch active: grouped score + stability + exhaustion + MACD regime + volume profile")
    base.main()


if __name__ == "__main__":
    main()
