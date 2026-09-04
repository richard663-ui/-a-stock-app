# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import services.train_l1_60s_model_v4 as t


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
    for i in range(480):
        dt = base + timedelta(seconds=5 * i)
        cls = (i + day_index) % 3  # 0 up, 1 down, 2 flat
        up = cls == 0
        down = cls == 1
        ret = 0.060 if up else (-0.060 if down else 0.0)
        sign = 1.0 if up else (-1.0 if down else 0.0)
        # Add periodic reversal-like observations so V4 regime fields execute.
        rev = (i % 11 == 0) and cls != 2
        c10 = (-0.025 * sign) if rev else (0.030 * sign)
        f = {
            "minute_of_day": dt.hour * 60 + dt.minute + dt.second / 60.0,
            "spread_pct": 0.02 + (i % 5) * 0.001,
            "depth5_imbalance_pct": 38.0 * sign,
            "microprice_vs_mid_pct": 0.012 * sign,
            "day_return_pct": (i / 480.0 - 0.5) * 0.5,
            "distance_high_pct": -0.1 if up else -0.7,
            "distance_low_pct": 0.7 if up else 0.1,
            "change_10s_pct": c10,
            "change_30s_pct": 0.050 * sign,
            "change_60s_pct": 0.075 * sign,
            "change_120s_pct": 0.090 * sign,
            "above_vwap_pct": 0.045 * sign,
            "tick_buy_pct": 67.0 if up else (33.0 if down else 50.0),
            "book_buy_pressure_pct": 68.0 if up else (32.0 if down else 50.0),
            "pressure_change_pct": 6.0 * sign,
            "volume": 100000 + i * (160 if cls != 2 else 60) + day_index * 1000,
            "amount": 2000000 + i * (2600 if cls != 2 else 900) + day_index * 10000,
            "market_hs300_return_pct": 0.01 * day_index,
            "market_chinext_return_pct": 0.015 * day_index,
            "relative_to_hs300_pct": 0.025 * sign,
            "relative_to_chinext_pct": 0.020 * sign,
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
            rc = t.train("301236.SZ", 200, root, 2.0)
            assert rc == 0, f"V4 trainer rc={rc}"
            report_path = t.MODEL_DIR / "301236_SZ_training_report_latest.json"
            assert report_path.exists(), "V4 training report missing"
            obj = json.loads(report_path.read_text(encoding="utf-8"))
            assert obj.get("trainer_version") == t.TRAINER_VERSION
            assert obj.get("architecture") == "TWO_BINARY_HEADS_UP_ENTRY_AND_DOWN_RISK"
            assert obj.get("samples_train_thinned_15s", 0) > 0
            assert obj.get("samples_test_nonoverlap", 0) > 0
            assert set(obj.get("models", {})) == {"logistic_balanced", "hist_gradient_boosting"}
            for item in obj["models"].values():
                assert set(item.get("heads", {})) == {"up_entry", "down_risk"}
                assert "selected_threshold_test_nonoverlap" in item
                assert item.get("threshold_source") == "VALIDATION_NONOVERLAP"
        finally:
            t.MODEL_DIR = old_model_dir
    print("PASS: L1 V4 synthetic DB -> regime features -> asymmetric heads -> OOS report")


if __name__ == "__main__":
    main()
