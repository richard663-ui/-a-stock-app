# -*- coding: utf-8 -*-
from __future__ import annotations

import numpy as np
import pandas as pd

import services.opening_core_audit_v1 as m


def main() -> int:
    f = pd.DataFrame({
        "minute_of_day": [569.9, 570.0, 599.0, 629.9, 630.0],
        "label_actionable_smoothed_mid_60": [0, 1, -1, 1, 0],
        "ret_smoothed_mid_60_pct": [0.0, 0.05, -0.04, 0.03, 0.0],
        "ret_ask_to_bid_60_pct": [0.0, 0.04, -0.01, 0.03, 0.0],
        "bid1": [10.0] * 5,
        "future_bid_60": [10.0, 10.04, 9.96, 10.03, 10.0],
    })
    o = m._opening(f)
    assert len(o) == 3
    assert o["minute_of_day"].min() >= 570.0 and o["minute_of_day"].max() < 630.0

    pred = np.array([1, -1, 1], dtype=int)
    x = o.copy()
    metrics = m._metrics(x, pred, full_test_rows=20)
    assert metrics["directional_predictions"] == 3
    assert abs(float(metrics["directional_coverage_pct"]) - 15.0) < 1e-9

    good = {
        "directional_predictions": 150, "directional_accuracy_pct": 60.0,
        "directional_coverage_pct": 8.0, "avg_net_edge_bp": 1.2,
        "positive_net_edge_days": 4, "accuracy_wilson_95_lower_pct": 52.5,
    }
    base = {"directional_accuracy_pct": 46.8, "avg_net_edge_bp": -1.15}
    assert m._gate(good, base)["pass"] is True
    bad = dict(good); bad["directional_coverage_pct"] = 4.9
    assert m._gate(bad, base)["pass"] is False
    print("opening_core_audit_v1 self-test PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
