from __future__ import annotations

import numpy as np
import pandas as pd

import services.v4r_imacd_filter_audit_v1 as m


def main() -> None:
    n = 120
    frame = pd.DataFrame({
        "ret_smoothed_mid_60_pct": np.r_[np.full(60, 0.04), np.full(60, -0.04)],
        "label_actionable_smoothed_mid_60": np.r_[np.ones(60, dtype=int), -np.ones(60, dtype=int)],
        "ret_ask_to_bid_60_pct": np.r_[np.full(60, 0.04), np.full(60, -0.01)],
        "bid1": np.full(n, 10.0),
        "future_bid_60": np.r_[np.full(60, 10.04), np.full(60, 9.96)],
        "imacd_state_up": np.r_[np.ones(50), np.zeros(70)],
        "imacd_state_down": np.r_[np.zeros(70), np.ones(50)],
        "imacd_up_exhaust": np.zeros(n),
        "imacd_down_exhaust": np.zeros(n),
        "imacd_state": np.r_[np.full(50, "ACCEL_UP"), np.full(20, "NEUTRAL"), np.full(50, "ACCEL_DOWN")],
    })
    pred = np.r_[np.ones(60, dtype=int), -np.ones(60, dtype=int)]
    strict = m._filter_mask(frame, pred, "STRICT_ALIGN")
    assert int(strict.sum()) == 100
    metrics = m._metrics(frame, pred, strict)
    assert metrics["directional_accuracy_pct"] == 100.0
    assert metrics["directional_coverage_pct"] > 80.0
    chosen, diag = m._select_on_validation(frame, pred)
    assert chosen in m.FILTERS
    assert set(diag) == set(m.FILTERS)
    gate = m._gate({
        "directional_predictions": 200, "directional_accuracy_pct": 60.0,
        "directional_coverage_pct": 10.0, "avg_net_edge_bp": 1.0,
        "positive_net_edge_days": 4, "accuracy_wilson_95_lower_pct": 53.0,
    }, {"directional_accuracy_pct": 47.0, "avg_net_edge_bp": -1.0})
    assert gate["pass"] is True
    print("selftest_v4r_imacd_filter_audit_v1: PASS")


if __name__ == "__main__":
    main()
