# -*- coding: utf-8 -*-
from __future__ import annotations

import numpy as np
import pandas as pd

import services.train_l1_60s_model_v4 as core
import services.v4r_direction_asymmetry_audit_v1 as audit


def main() -> int:
    n = 200
    pred = np.r_[np.ones(100, dtype=int), -np.ones(100, dtype=int)]
    # UP side has +8bp gross and is correct; DOWN side loses 4bp gross-equivalent.
    ret = np.r_[np.full(100, 0.08), np.full(100, 0.04)]
    y = np.r_[np.ones(100, dtype=int), np.zeros(100, dtype=int)]
    frame = pd.DataFrame({
        core.RET_TARGET: ret,
        core.ACTION_TARGET: y,
        "ret_ask_to_bid_60_pct": ret,
        "bid1": np.full(n, 10.0),
        "future_bid_60": np.r_[np.full(100, 10.08), np.full(100, 10.04)],
    })
    selected, diag = audit._select(frame, pred)
    assert selected == "UP_ONLY", (selected, diag)
    m = audit._metrics(frame, pred, selected)
    assert m["directional_predictions"] == 100
    assert m["directional_accuracy_pct"] == 100.0
    assert m["avg_net_edge_bp"] > 0

    baseline = audit._metrics(frame, pred, "BOTH")
    candidate = dict(m)
    candidate.update({"positive_net_edge_days": 3, "accuracy_wilson_95_lower_pct": 95.0})
    baseline.update({"positive_net_edge_days": 0, "accuracy_wilson_95_lower_pct": 0.0})
    gate = audit._gate(candidate, baseline)
    # Coverage is 50%, n=100, positive net, and candidate beats baseline.
    assert gate["pass"], gate
    print("[PASS] V4R direction asymmetry validation-only selection/gate self-test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
