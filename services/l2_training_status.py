# -*- coding: utf-8 -*-
"""Compact status report for the persistent L2 training dataset."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

DATA_ROOT = Path.home() / "AStockData"


def _load(data_root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted((data_root / "training").glob("*/l2_training.sqlite3")):
        conn = sqlite3.connect(path, timeout=10.0)
        try:
            ok = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_samples_v1'").fetchone()
            if not ok:
                continue
            cur = conn.execute(
                "SELECT symbol,generated_at,true_l2,core_l2_ready,l2_available_count,valid,invalid_reason,"
                "label_last_60,label_mid_60,label_smoothed_mid_60,ret_last_60_pct,ret_mid_60_pct,ret_smoothed_mid_60_pct "
                "FROM training_samples_v1 ORDER BY generated_ts"
            )
            for r in cur.fetchall():
                rows.append({
                    "symbol": r[0], "generated_at": r[1], "true_l2": bool(r[2]), "core_l2_ready": bool(r[3]),
                    "l2_available_count": int(r[4] or 0), "valid": bool(r[5]), "invalid_reason": r[6],
                    "label_last_60": r[7], "label_mid_60": r[8], "label_smoothed_mid_60": r[9],
                    "ret_last_60_pct": r[10], "ret_mid_60_pct": r[11], "ret_smoothed_mid_60_pct": r[12],
                    "source": str(path),
                })
        finally:
            conn.close()
    return rows


def main() -> None:
    rows = _load(DATA_ROOT)
    if not rows:
        print("No L2 training samples yet.")
        print("Run update_l2_training_pipeline.bat, keep QMT logged in, and collect during market hours.")
        return
    print("=" * 78)
    print("AStock L2 training dataset status")
    print("=" * 78)
    days = sorted({Path(r["source"]).parent.name for r in rows})
    print(f"Trading days stored: {len(days)}  {days[0]} -> {days[-1]}")
    print(f"Samples total: {len(rows)}")
    print(f"Valid 60s labels: {sum(r['valid'] for r in rows)}")
    print(f"True L2 transaction available: {sum(r['true_l2'] for r in rows)}")
    print(f"Core L2 ready: {sum(r['core_l2_ready'] for r in rows)}")
    print()
    symbols = sorted({r["symbol"] for r in rows})
    for s in symbols:
        a = [r for r in rows if r["symbol"] == s]
        valid = [r for r in a if r["valid"]]
        tl2 = [r for r in valid if r["true_l2"]]
        cls = Counter(str(r["label_smoothed_mid_60"]) for r in tl2)
        avg_l2 = sum(r["l2_available_count"] for r in a) / len(a) if a else 0.0
        print(
            f"{s:<12} samples={len(a):<5} valid={len(valid):<5} trueL2={len(tl2):<5} "
            f"avgFeeds={avg_l2:.1f} labels(-1/0/1)={cls.get('-1',0)}/{cls.get('0',0)}/{cls.get('1',0)}"
        )
    invalid = Counter(str(r["invalid_reason"] or "UNKNOWN") for r in rows if not r["valid"] and r["invalid_reason"])
    if invalid:
        print("\nInvalid reasons:")
        for k, v in invalid.most_common():
            print(f"  {k}: {v}")
    print("\nPrimary training target: smoothed mid-price at +55s..+65s centered on 60s.")
    print("Legacy lastPrice and point mid-price labels are retained for comparison.")


if __name__ == "__main__":
    main()
