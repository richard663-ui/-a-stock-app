# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import services.qmt_l1_60s_walkforward_v3 as w
import services.selftest_l1_trainer_v6 as synth


def main() -> None:
    v6_watch = {
        "trainer_version": "l1-60s-trainer-v6-exec-aligned-stock-intercept-robust-20260905",
        "models": {"logistic_balanced": {"selected_probability_threshold": {"up_entry": .999, "down_risk": .999}}},
    }
    assert w._thresholds_ok(v6_watch, "V6")[0]
    bad = {
        "trainer_version": "wrong",
        "models": {"logistic_balanced": {"selected_probability_threshold": {"up_entry": .99, "down_risk": .50}}},
    }
    ok, problems = w._thresholds_ok(bad, "V6")
    assert not ok and problems

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        dataset = root / "dataset"
        audit = root / "audit"
        start = datetime(2026, 8, 31)
        dates = []
        for j in range(5):
            day = start + timedelta(days=j)
            dates.append(day.strftime("%Y-%m-%d"))
            synth._write_day(dataset, day, j)

        fold = w._run_fold(dataset, audit, dates, 1)
        assert fold["v4r_rc"] == 0, fold
        assert fold["v5r_rc"] == 0, fold
        assert fold["v6_rc"] == 0, fold
        assert fold["audit_invariants"]["v4r"]["ok"] is True
        assert fold["audit_invariants"]["v5r"]["ok"] is True
        assert fold["audit_invariants"]["v6"]["ok"] is True
        for variant in ("v4r", "v5r", "v6"):
            report = fold[variant]
            assert "logistic_balanced" in (report.get("models") or {}), variant
            assert "selected_threshold_test_nonoverlap" in report["models"]["logistic_balanced"], variant

        folds = [fold]
        for variant in ("V4R", "V5R", "V6"):
            agg = w.base._aggregate(folds, variant, "logistic_balanced")
            assert agg.get("folds") == 1, (variant, agg)
            assert agg.get("test_rows", 0) > 0, (variant, agg)
            gate = w._candidate_gate(agg)
            assert "pass" in gate and "checks" in gate

    print("PASS: QMT walk-forward V3 runs same-fold V4R/V5R/V6 comparison without promotion or deployment")


if __name__ == "__main__":
    main()
