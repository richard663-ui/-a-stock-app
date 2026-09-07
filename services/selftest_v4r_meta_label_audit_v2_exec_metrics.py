# -*- coding: utf-8 -*-
from __future__ import annotations

import numpy as np
import pandas as pd

import services.v4r_meta_label_audit_v2_exec_metrics as v2


def main() -> int:
    frame = pd.DataFrame({
        "meta_exact_signed_edge_bp": [5.0, 1.0, 4.0, -3.0],
    })
    pred = np.array([1, 1, -1, -1])
    keep = np.array([True, True, True, True])
    m = v2._metrics(frame, pred, keep)
    assert m["directional_predictions"] == 4, m
    assert abs(float(m["directional_accuracy_pct"]) - 50.0) < 1e-9, m
    assert abs(float(m["avg_gross_edge_bp"]) - 1.75) < 1e-9, m
    assert abs(float(m["avg_net_edge_bp"]) - (-0.25)) < 1e-9, m
    assert m["up_predictions"] == 2 and m["down_predictions"] == 2, m

    folds = [
        {"test_day": "2026-09-01", "meta_threshold": 0.6, "candidate": {
            "test_rows": 100, "directional_predictions": 20,
            "directional_accuracy_pct": 60.0, "avg_gross_edge_bp": 4.0,
            "avg_net_edge_bp": 2.0,
        }},
        {"test_day": "2026-09-02", "meta_threshold": 0.6, "candidate": {
            "test_rows": 100, "directional_predictions": 30,
            "directional_accuracy_pct": 40.0, "avg_gross_edge_bp": 1.0,
            "avg_net_edge_bp": -1.0,
        }},
    ]
    a = v2._aggregate(folds, "candidate")
    assert a["directional_predictions"] == 50, a
    assert abs(float(a["directional_accuracy_pct"]) - 48.0) < 1e-9, a
    assert abs(float(a["avg_gross_edge_bp"]) - 2.2) < 1e-9, a
    assert abs(float(a["avg_net_edge_bp"]) - 0.2) < 1e-9, a
    assert a["positive_net_edge_days"] == 1, a
    print("v4r_meta_label_audit_v2_exec_metrics selftest PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
