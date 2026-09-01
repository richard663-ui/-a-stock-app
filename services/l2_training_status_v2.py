# -*- coding: utf-8 -*-
"""Status report for the persistent Level-2 ML dataset."""
from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

DATA_ROOT = Path.home() / "AStockData"


def _rows() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in sorted((DATA_ROOT / "training").glob("*/l2_training.sqlite3")):
        conn = sqlite3.connect(path, timeout=10.0)
        try:
            ok = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_samples_v2'").fetchone()
            if not ok:
                continue
            cur = conn.execute(
                "SELECT symbol,generated_at,true_l2,core_l2_ready,l2_available_count,valid,invalid_reason,"
                "label_last_60,label_mid_60,label_smoothed_mid_60,ret_last_60_pct,ret_mid_60_pct,ret_smoothed_mid_60_pct "
                "FROM training_samples_v2 ORDER BY generated_ts"
            )
            for r in cur.fetchall():
                out.append({
                    "symbol": r[0], "generated_at": r[1], "true_l2": bool(r[2]), "core_l2_ready": bool(r[3]),
                    "feeds": int(r[4] or 0), "valid": bool(r[5]), "invalid_reason": r[6],
                    "last_label": r[7], "mid_label": r[8], "smooth_label": r[9],
                    "last_ret": r[10], "mid_ret": r[11], "smooth_ret": r[12], "db": str(path),
                })
        finally:
            conn.close()
    return out


def _event_count() -> int:
    total = 0
    for path in sorted((DATA_ROOT / "training").glob("*/l2_training.sqlite3")):
        conn = sqlite3.connect(path, timeout=10.0)
        try:
            ok = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='l2_events_v2'").fetchone()
            if ok:
                total += int(conn.execute("SELECT COUNT(*) FROM l2_events_v2").fetchone()[0])
        finally:
            conn.close()
    return total


def main() -> None:
    rows = _rows()
    if not rows:
        print("No training_samples_v2 yet.")
        print("Keep QMT logged in and run the L2 training recorder during market hours.")
        return
    days = sorted({Path(r["db"]).parent.name for r in rows})
    print("=" * 78)
    print("AStock Level-2 ML dataset status")
    print("=" * 78)
    print(f"Trading days: {len(days)}  {days[0]} -> {days[-1]}")
    print(f"Samples: {len(rows)}  Valid labels: {sum(r['valid'] for r in rows)}")
    print(f"True L2 samples: {sum(r['true_l2'] for r in rows)}  Core-L2 ready: {sum(r['core_l2_ready'] for r in rows)}")
    print(f"Persisted raw L2 events: {_event_count()}")
    print()
    for symbol in sorted({r["symbol"] for r in rows}):
        a = [r for r in rows if r["symbol"] == symbol]
        valid = [r for r in a if r["valid"]]
        tl2 = [r for r in valid if r["true_l2"]]
        labels = Counter(str(r["smooth_label"]) for r in tl2)
        avg_feeds = sum(r["feeds"] for r in a) / len(a) if a else 0.0
        print(
            f"{symbol:<12} samples={len(a):<5} valid={len(valid):<5} trueL2={len(tl2):<5} "
            f"avgFeeds={avg_feeds:.1f} smoothLabels(-1/0/1)={labels.get('-1',0)}/{labels.get('0',0)}/{labels.get('1',0)}"
        )
    bad = Counter(str(r["invalid_reason"] or "UNKNOWN") for r in rows if not r["valid"] and r["invalid_reason"])
    if bad:
        print("\nInvalid labels:")
        for reason, n in bad.most_common():
            print(f"  {reason}: {n}")
    print("\nPrimary target = mean(mid-price +55s..+65s) versus entry mid-price.")
    print("lastPrice@60s and point midPrice@60s remain only as comparison labels.")


if __name__ == "__main__":
    main()
