# -*- coding: utf-8 -*-
from __future__ import annotations

import math

import pandas as pd

import services.l1_v6_shadow_runner_v1 as shadow


def main() -> None:
    t0 = 1_800_000_000.0
    payload = {
        "bridge_id": "test",
        "sample_key": "301236.SZ:30000000",
        "symbol": "301236.SZ",
        "generated_at": "2027-01-15T09:30:00+08:00",
        "generated_ts": t0,
        "model_version": "v6@test",
        "model_scope": "ALL",
        "model_family": "logistic_balanced",
        "up_prob": 0.8,
        "down_prob": 0.1,
        "up_threshold": 0.7,
        "down_threshold": 0.7,
        "direction": "UP",
        "phase": "OPEN_CORE_0930_1030",
        "entry_bid": 10.00,
        "entry_ask": 10.01,
        "entry_mid": 10.005,
        "status": "PENDING",
        "diagnostic": {},
    }
    future = pd.DataFrame([
        {"generated_ts": t0 + 55, "bid1": 10.02, "ask1": 10.03, "mid_price": 10.025},
        {"generated_ts": t0 + 60, "bid1": 10.03, "ask1": 10.04, "mid_price": 10.035},
        {"generated_ts": t0 + 65, "bid1": 10.04, "ask1": 10.05, "mid_price": 10.045},
    ])
    settled = shadow._settlement_from_rows(payload, future)
    assert settled is not None
    assert settled["status"] == "SETTLED"
    assert math.isclose(settled["future_bid_smoothed_60"], 10.03, rel_tol=0, abs_tol=1e-12)
    assert math.isclose(settled["future_bid_60"], 10.03, rel_tol=0, abs_tol=1e-12)
    expected_up_pct = (10.03 / 10.01 - 1.0) * 100.0
    assert math.isclose(settled["up_exec_return_pct"], expected_up_pct, rel_tol=0, abs_tol=1e-12)
    assert settled["up_actionable"] is True
    assert settled["correct"] is True
    assert math.isclose(settled["gross_edge_bp"], expected_up_pct * 100.0, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(settled["net_edge_bp"], expected_up_pct * 100.0 - shadow.HURDLE_BP, rel_tol=0, abs_tol=1e-9)
    assert settled["diagnostic"]["settlement_is_observed_bid_not_proxy"] is True

    too_thin = future.iloc[[1]].copy()
    assert shadow._settlement_from_rows(payload, too_thin) is None
    assert shadow._phase(570.0) == "OPEN_CORE_0930_1030"
    assert shadow._phase(650.0) == "AM_LATE_1030_1130"
    assert shadow._phase(800.0) == "PM_1300_1500"

    print("L1 V6 shadow runner self-test PASS")


if __name__ == "__main__":
    main()
