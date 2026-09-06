# -*- coding: utf-8 -*-
from __future__ import annotations

import numpy as np
import pandas as pd

import services.v4r_meta_label_audit_v1 as meta


def main() -> int:
    forbidden = ("future", "ret_", "label_", "target")
    assert all(not any(tok in f for tok in forbidden) for f in meta.META_FEATURES), meta.META_FEATURES
    assert meta.META_THRESHOLDS == (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
    frame = pd.DataFrame({
        "spread_pct": [0.01, 0.01],
        "change_10s_pct": [0.02, -0.02], "change_30s_pct": [0.03, -0.03], "change_60s_pct": [0.04, -0.04],
        "above_vwap_pct": [0.01, -0.01], "tick_buy_pct": [60.0, 40.0], "book_buy_pressure_pct": [58.0, 42.0],
        "pressure_change_pct": [2.0, -2.0], "microprice_vs_mid_pct": [0.01, -0.01], "depth5_imbalance_pct": [10.0, -10.0],
        "imacd_state_up": [1.0, 0.0], "imacd_state_down": [0.0, 1.0],
        "imacd_up_exhaust": [0.0, 0.0], "imacd_down_exhaust": [0.0, 0.0], "imacd_state": ["ACCEL_UP", "ACCEL_DOWN"],
        "minute_of_day": [590.0, 590.0],
        "ret_ask_to_bid_60_pct": [0.05, -0.01], "bid1": [10.0, 10.0], "future_bid_60": [10.005, 9.995],
    })
    up = np.array([0.75, 0.20]); dn = np.array([0.20, 0.75]); pred = np.array([1, -1])
    out = meta._meta_frame(frame, up, dn, pred, 0.60, 0.60)
    assert out["meta_tradeable"].tolist() == [1, 1], out[["meta_exact_signed_edge_bp", "meta_tradeable"]]
    assert out["meta_imacd_aligned"].tolist() == [1.0, 1.0]
    assert np.all(np.isfinite(out[meta.META_FEATURES].to_numpy(float)))
    print("v4r_meta_label_audit_v1 selftest PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
