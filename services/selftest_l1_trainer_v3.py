# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import services.train_l1_60s_model_v3 as t


def _write_day(root: Path, day: datetime, day_index: int) -> None:
    folder = root / "training" / day.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "l2_training.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE training_samples_v2 (
            symbol TEXT, sample_bucket INTEGER, generated_ts REAL, generated_at TEXT,
            session TEXT, label_threshold_pct REAL, ret_smoothed_mid_60_pct REAL,
            labeled_at TEXT, valid INTEGER, features_json TEXT
        )
    """)
    base = day.replace(hour=9, minute=30, second=0, microsecond=0)
    rows = []
    for i in range(420):
        dt = base + timedelta(seconds=5 * i)
        # Stable mixture of UP / FLAT / DOWN with weak learnable structure.
        cls = (i + day_index) % 3
        ret = 0.055 if cls == 0 else (-0.055 if cls == 1 else 0.0)
        f = {
            "minute_of_day": dt.hour * 60 + dt.minute + dt.second / 60.0,
            "spread_pct": 0.02 + (i % 5) * 0.001,
            "depth5_imbalance_pct": 35.0 if cls == 0 else (-35.0 if cls == 1 else 0.0),
            "microprice_vs_mid_pct": 0.01 if cls == 0 else (-0.01 if cls == 1 else 0.0),
            "day_return_pct": (i / 420.0 - 0.5) * 0.6,
            "distance_high_pct": -0.5, "distance_low_pct": 0.5,
            "change_10s_pct": 0.03 if cls == 0 else (-0.03 if cls == 1 else 0.0),
            "change_30s_pct": 0.05 if cls == 0 else (-0.05 if cls == 1 else 0.0),
            "change_60s_pct": 0.07 if cls == 0 else (-0.07 if cls == 1 else 0.0),
            "change_120s_pct": 0.08 if cls == 0 else (-0.08 if cls == 1 else 0.0),
            "above_vwap_pct": 0.04 if cls == 0 else (-0.04 if cls == 1 else 0.0),
            "tick_buy_pct": 65.0 if cls == 0 else (35.0 if cls == 1 else 50.0),
            "book_buy_pressure_pct": 65.0 if cls == 0 else (35.0 if cls == 1 else 50.0),
            "pressure_change_pct": 5.0 if cls == 0 else (-5.0 if cls == 1 else 0.0),
            "volume": 100000 + i * 100 + day_index * 1000,
            "amount": 2000000 + i * 2000 + day_index * 10000,
            "market_hs300_return_pct": 0.01 * day_index,
            "market_chinext_return_pct": 0.015 * day_index,
            "relative_to_hs300_pct": 0.02 if cls == 0 else (-0.02 if cls == 1 else 0.0),
            "relative_to_chinext_pct": 0.015 if cls == 0 else (-0.015 if cls == 1 else 0.0),
        }
        ts = dt.timestamp()
        rows.append((
            "301236.SZ", int(ts // 5), ts, dt.isoformat(), "AM", 0.01, ret,
            (dt + timedelta(seconds=66)).isoformat(), 1, json.dumps(f),
        ))
    conn.executemany("INSERT INTO training_samples_v2 VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit(); conn.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        start = datetime(2026, 9, 1)
        for j in range(3):
            _write_day(root, start + timedelta(days=j), j)
        old_model_dir = t.MODEL_DIR
        try:
            t.MODEL_DIR = root / "models" / "l1_60s"
            rc = t._fallback_train("301236.SZ", 200, root, 2.0)
            assert rc == 0, f"fallback trainer rc={rc}"
            report = t.MODEL_DIR / "301236_SZ_training_report_latest.json"
            assert report.exists(), "training report missing"
            obj = json.loads(report.read_text(encoding="utf-8"))
            assert obj.get("trainer_version") == t.TRAINER_VERSION
            assert obj.get("samples_test_nonoverlap", 0) > 0
            assert set(obj.get("models", {})) == {"logistic_balanced", "hist_gradient_boosting"}
        finally:
            t.MODEL_DIR = old_model_dir
    print("PASS: L1 V3 synthetic DB -> features -> split -> fit -> report")


if __name__ == "__main__":
    main()
