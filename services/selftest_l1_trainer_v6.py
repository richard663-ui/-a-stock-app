# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import services.train_l1_60s_model_v6_exec_aligned as v6


def _write_day(root: Path, day: datetime, day_index: int) -> None:
    folder = root / "training" / day.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(folder / "l2_training.sqlite3")
    conn.execute("""
        CREATE TABLE training_samples_v2 (
            symbol TEXT, sample_bucket INTEGER, generated_ts REAL, generated_at TEXT,
            session TEXT, bid1 REAL, ask1 REAL, mid_price REAL, spread_pct REAL,
            label_threshold_pct REAL, ret_smoothed_mid_60_pct REAL,
            labeled_at TEXT, valid INTEGER, features_json TEXT
        )
    """)
    rows = []
    symbols = ("301236.SZ", "000400.SZ")
    base = day.replace(hour=9, minute=30, second=0, microsecond=0)
    for si, symbol in enumerate(symbols):
        for i in range(360):
            dt = base + timedelta(seconds=5 * i)
            cls = (i + day_index + si) % 3
            sign = 1.0 if cls == 0 else (-1.0 if cls == 1 else 0.0)
            # 2bp spread. UP has enough future mid move to clear spread + 2bp
            # economic hurdle; DOWN has a clear bid->future-bid decline.
            spread_pct = 0.020 + 0.002 * si
            ret = 0.085 if cls == 0 else (-0.065 if cls == 1 else 0.004)
            mid = 20.0 + si * 10.0 + day_index * 0.05 + i * 0.0005
            half = spread_pct / 200.0
            bid = mid * (1.0 - half)
            ask = mid * (1.0 + half)
            f = {
                "minute_of_day": dt.hour * 60 + dt.minute + dt.second / 60.0,
                "spread_pct": spread_pct,
                "depth5_imbalance_pct": 35.0 * sign + si * 3.0,
                "microprice_vs_mid_pct": 0.010 * sign,
                "day_return_pct": 0.2 * sign + day_index * 0.01,
                "distance_high_pct": -0.08 if sign > 0 else -0.55,
                "distance_low_pct": 0.65 if sign > 0 else 0.10,
                "change_10s_pct": 0.028 * sign,
                "change_30s_pct": 0.045 * sign,
                "change_60s_pct": 0.070 * sign,
                "change_120s_pct": 0.080 * sign,
                "above_vwap_pct": 0.035 * sign,
                "tick_buy_pct": 66.0 if sign > 0 else (34.0 if sign < 0 else 50.0),
                "book_buy_pressure_pct": 67.0 if sign > 0 else (33.0 if sign < 0 else 50.0),
                "pressure_change_pct": 4.0 * sign,
                "volume": 100000 + i * (140 + 10 * si) + day_index * 1000,
                "amount": 2500000 + i * (2400 + 100 * si) + day_index * 12000,
                "market_hs300_return_pct": day_index * 0.01,
                "market_chinext_return_pct": day_index * 0.015,
                "relative_to_hs300_pct": 0.02 * sign,
                "relative_to_chinext_pct": 0.018 * sign,
            }
            ts = dt.timestamp()
            rows.append((
                symbol, int(ts // 5), ts, dt.isoformat(), "AM",
                bid, ask, mid, spread_pct, 0.01, ret,
                (dt + timedelta(seconds=66)).isoformat(), 1, json.dumps(f),
            ))
    conn.executemany("INSERT INTO training_samples_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit(); conn.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        start = datetime(2026, 8, 31)
        for j in range(5):
            _write_day(root, start + timedelta(days=j), j)

        old_dir = v6.MODEL_DIR
        try:
            v6.MODEL_DIR = root / "models" / "v6_exec_aligned"
            rc = v6.train("ALL", 600, root, 2.0)
            assert rc == 0, f"V6 trainer rc={rc}"
            report_path = v6.MODEL_DIR / "ALL_training_report_latest.json"
            assert report_path.exists(), "V6 report missing"
            obj = json.loads(report_path.read_text(encoding="utf-8"))
            assert obj.get("trainer_version") == v6.TRAINER_VERSION
            assert obj.get("candidate_role") == "V6_CHALLENGER_ONLY"
            assert obj.get("test_used_for_selection") is False
            assert obj.get("eligible_for_live_deployment") is False
            assert obj.get("target_policy") == v6.TARGET_POLICY
            feats = set(obj.get("features") or [])
            assert set(v6.SYMBOL_FEATURES).issubset(feats), "stock intercepts missing"
            assert set(obj.get("models") or {}) == {"logistic_balanced", "hist_gradient_boosting"}
            for name, item in obj["models"].items():
                assert item.get("candidate_role") == ("PROMOTION_CANDIDATE" if name == "logistic_balanced" else "CONTROL_ONLY")
                assert "selected_threshold_test_nonoverlap" in item
                assert item.get("readiness", {}).get("eligible_for_live_deployment") is False

            prepared = v6._prepare_exec_aligned("ALL", root, 2.0)
            assert not prepared.empty
            assert prepared[v6.UP_EXEC_RET].notna().all()
            assert prepared[v6.DOWN_HOLD_RET].notna().all()
            # Economic target must be stricter than plain future-mid direction:
            # tiny +0.4bp mid moves are not UP entries after spread.
            tiny = prepared[prepared[v6.core.RET_TARGET].between(0.003, 0.005)]
            assert len(tiny) > 0 and int(tiny[v6.core.UP_TARGET].sum()) == 0
            # Fixed stock identities are deterministic current-time features.
            assert (prepared[v6.SYMBOL_FEATURES].sum(axis=1) == 1.0).all()
        finally:
            v6.MODEL_DIR = old_dir
    print("PASS: L1 V6 -> execution-aligned targets -> stock intercepts -> robust scaler -> frozen V5 gate -> OOS report")


if __name__ == "__main__":
    main()
