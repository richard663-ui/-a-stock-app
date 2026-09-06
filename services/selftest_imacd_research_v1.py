# -*- coding: utf-8 -*-
from __future__ import annotations

import numpy as np
import pandas as pd

import services.imacd_research_audit_v1 as m


def main() -> None:
    rows = []
    start = 1_800_000_000.0
    for symbol_i, symbol in enumerate(("301236.SZ", "000400.SZ")):
        price = 20.0 + symbol_i * 5.0
        for i in range(240):
            # Deterministic trend changes create positive/negative MACD states without future input.
            drift = 0.002 if (i // 60) % 2 == 0 else -0.002
            price = max(1.0, price + drift + 0.0005 * np.sin(i / 4.0))
            rows.append({
                "symbol": symbol, "trade_date": "2026-09-01", "session": "AM",
                "generated_ts": start + i * 5, "last_price": price,
                "ret_ask_to_bid_60_pct": 0.04 if drift > 0 else -0.01,
                "down_hold_return_pct": -0.04 if drift < 0 else 0.01,
                "minute_of_day": 570 + i * 5 / 60.0,
                "above_vwap_pct": drift * 10,
                "tick_buy_centered": 12.0 if drift > 0 else -12.0,
                "book_pressure_centered": 10.0 if drift > 0 else -10.0,
                "pressure_change_pct": 3.0 if drift > 0 else -3.0,
            })
    df = m._add_imacd(pd.DataFrame(rows))
    assert len(df) == len(rows)
    for c in m.IMACD:
        assert c in df.columns, c
    assert set(df["imacd_state"].dropna().unique()) <= {
        "WHIPSAW", "EARLY_UP", "EARLY_DOWN", "ACCEL_UP", "ACCEL_DOWN", "UP_EXHAUST", "DOWN_EXHAUST", "NEUTRAL"
    }
    assert not any(x.startswith(("future_", "ret_", "label_")) for x in m.IMACD)

    te = df.dropna(subset=["imacd5_hist_pct", "imacd15_hist_pct"]).tail(120).copy()
    te["up_target"] = (te["ret_ask_to_bid_60_pct"] > 0.02).astype(int)
    te["down_target"] = (te["down_hold_return_pct"] < -0.02).astype(int)
    up_p = np.where(te["imacd_state_up"].to_numpy() > 0.5, 0.9, 0.1)
    dn_p = np.where(te["imacd_state_down"].to_numpy() > 0.5, 0.9, 0.1)
    metric = m._evaluate(te, up_p, dn_p, 0.8, 0.8)
    assert metric["n"] == len(te)
    assert metric["directional_predictions"] >= 0
    assert metric["directional_coverage_pct"] is not None

    base = {"directional_accuracy_pct": 46.78, "avg_net_edge_bp": -1.15}
    fake = {
        "directional_predictions": 150, "directional_accuracy_pct": 60.0,
        "directional_coverage_pct": 8.0, "avg_net_edge_bp": 1.5,
        "positive_net_edge_days": 4, "accuracy_wilson_95_lower_pct": 53.0,
    }
    assert m._gate(fake, base)["pass"] is True
    fake["avg_net_edge_bp"] = -0.1
    assert m._gate(fake, base)["pass"] is False
    print("PASS: iMACD causal states, no future feature leakage, economic evaluation, and promotion gate")


if __name__ == "__main__":
    main()
