# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import services.qmt_l1_60s_walkforward_v2 as w
import services.selftest_l1_trainer_v4 as synth


def _copy_days(src: Path, dst: Path, dates: list[str]) -> None:
    for d in dates:
        s = src / "training" / d / "l2_training.sqlite3"
        t = dst / "training" / d / "l2_training.sqlite3"
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, t)


def _shift_live_feature(root: Path, day: str, name: str, add: float) -> None:
    path = root / "training" / day / "l2_training.sqlite3"
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT rowid,features_json FROM training_samples_v2").fetchall()
    payload = []
    for rid, text in rows:
        obj = json.loads(text)
        obj[name] = float(obj.get(name, 0.0)) + add
        payload.append((json.dumps(obj), rid))
    conn.executemany("UPDATE training_samples_v2 SET features_json=? WHERE rowid=?", payload)
    conn.commit(); conn.close()


def main() -> None:
    # Threshold invariants prevent V5's deliberate .999 abstention marker from
    # ever being mistaken for a valid V4 threshold.
    v4_bad = {"trainer_version": "l1-60s-trainer-v4r-asymmetric-rotating-thin-20260904",
              "models": {"logistic_balanced": {"selected_probability_threshold": {"up_entry": .999, "down_risk": .6}}}}
    ok, problems = w._thresholds_ok(v4_bad, "V4R")
    assert not ok and any("v4_threshold_out_of_range" in x for x in problems)
    v5_watch = {"trainer_version": "l1-60s-trainer-v5r-robust-challenger-20260904",
                "models": {"logistic_balanced": {"selected_probability_threshold": {"up_entry": .999, "down_risk": .999}}}}
    assert w._thresholds_ok(v5_watch, "V5R")[0]

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        hist = root / "hist"
        live = root / "live"
        start = datetime(2026, 8, 31)
        dates = []
        for j in range(5):
            dt = start + timedelta(days=j)
            dates.append(dt.strftime("%Y-%m-%d"))
            synth._write_day(hist, dt, j)
        _copy_days(hist, live, dates)

        same = w._historical_live_parity(hist, dates, live)
        assert same["matched_rows"] >= 1000, same
        assert same["status"] in {"PASS", "PASS_WITH_WARNINGS"}, same

        # Deliberately break one core stored feature across all live rows.  The
        # SQLite parity audit must notice it rather than silently trusting replay.
        for d in dates:
            _shift_live_feature(live, d, "tick_buy_pct", 100.0)
        bad = w._historical_live_parity(hist, dates, live)
        assert "tick_buy_pct" in bad.get("severe_feature_mismatches", []), bad

        # Unlike V1's null check, this produces ROC-AUC diagnostics even when a
        # robust challenger would abstain from every trade.
        null = w._effective_null_control(hist, dates, repeats=3)
        assert null.get("ok") is True, null
        assert null.get("shuffled_auc_median") is not None, null
        assert len(null.get("shuffled_label_runs") or []) == 3, null

    print("PASS: QMT walk-forward V2 detects parity breaks, validates thresholds, and runs threshold-independent null AUC")


if __name__ == "__main__":
    main()
