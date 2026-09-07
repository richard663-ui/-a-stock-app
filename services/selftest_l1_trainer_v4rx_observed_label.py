# -*- coding: utf-8 -*-
from __future__ import annotations

import math

import pandas as pd

import services.train_l1_60s_model_v4 as core
import services.train_l1_60s_model_v4rx_observed_label as v4rx


def main() -> None:
    rows = []
    # Symbol A: entry ask=20.01, future observed bids average 20.03 -> clear UP.
    for ts, bid in [(0, 20.00), (55, 20.03), (60, 20.03), (65, 20.03)]:
        rows.append({
            "symbol": "AAA.SZ", "trade_date": "2026-09-07", "generated_ts": float(ts),
            "bid1": bid, "ask1": bid + 0.01, "label_threshold_pct": 0.01,
        })
    # Symbol B: entry bid=100.00, future observed bids average 99.95 -> clear DOWN.
    for ts, bid in [(1000, 100.00), (1055, 99.95), (1060, 99.95), (1065, 99.95)]:
        rows.append({
            "symbol": "BBB.SH", "trade_date": "2026-09-07", "generated_ts": float(ts),
            "bid1": bid, "ask1": bid + 0.01, "label_threshold_pct": 0.01,
        })
    # Same symbol next day must never leak backward into the previous day's label.
    rows.append({
        "symbol": "AAA.SZ", "trade_date": "2026-09-08", "generated_ts": 55.0,
        "bid1": 99.99, "ask1": 100.00, "label_threshold_pct": 0.01,
    })

    frame = pd.DataFrame(rows)
    attached = v4rx._attach_observed_future_bid(frame)

    a0 = attached[(attached.symbol == "AAA.SZ") & (attached.trade_date == "2026-09-07") & (attached.generated_ts == 0)].iloc[0]
    assert int(a0[v4rx.FUTURE_BID_N]) == 3
    assert math.isclose(float(a0[v4rx.FUTURE_BID_MEAN]), 20.03, rel_tol=0, abs_tol=1e-12)
    assert math.isclose(float(a0[v4rx.FUTURE_BID_POINT]), 20.03, rel_tol=0, abs_tol=1e-12)

    b0 = attached[(attached.symbol == "BBB.SH") & (attached.generated_ts == 1000)].iloc[0]
    assert int(b0[v4rx.FUTURE_BID_N]) == 3
    assert math.isclose(float(b0[v4rx.FUTURE_BID_MEAN]), 99.95, rel_tol=0, abs_tol=1e-12)

    labeled = v4rx._apply_exec_labels(attached, hurdle_bp=2.0)
    a = labeled[(labeled.symbol == "AAA.SZ") & (labeled.trade_date == "2026-09-07") & (labeled.generated_ts == 0)].iloc[0]
    b = labeled[(labeled.symbol == "BBB.SH") & (labeled.generated_ts == 1000)].iloc[0]

    # At ~20 CNY, one 0.01 tick is ~5bp, larger than the 2bp research floor.
    assert float(a[v4rx.LABEL_HURDLE]) > 0.049
    assert int(a[core.UP_TARGET]) == 1
    assert int(a[core.DOWN_TARGET]) == 0
    assert int(a[core.ACTION_TARGET]) == 1

    assert int(b[core.UP_TARGET]) == 0
    assert int(b[core.DOWN_TARGET]) == 1
    assert int(b[core.ACTION_TARGET]) == -1

    # Primary economic scores are direction-specific and use observed future bid.
    assert float(a[v4rx.UP_EXEC_RET]) > 0.0
    assert float(b[v4rx.DOWN_HOLD_RET]) < 0.0

    print("V4RX_OBSERVED_LABEL_SELFTEST_OK")


if __name__ == "__main__":
    main()
