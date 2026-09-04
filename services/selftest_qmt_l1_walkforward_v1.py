# -*- coding: utf-8 -*-
from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import services.qmt_l1_60s_walkforward_v1 as w

CN = ZoneInfo("Asia/Shanghai")


def _epoch_ms(local_dt: datetime) -> int:
    return int(local_dt.replace(tzinfo=CN).timestamp() * 1000)


def _raw_session(day: str, start: str, end: str, base: float, drift: float) -> pd.DataFrame:
    s = datetime.strptime(f"{day} {start}", "%Y-%m-%d %H:%M:%S")
    e = datetime.strptime(f"{day} {end}", "%Y-%m-%d %H:%M:%S")
    rows = []
    i = 0
    t = s
    volume = 100000.0
    amount = 0.0
    while t <= e:
        price = base + drift * i + 0.01 * math.sin(i / 9.0)
        bid = price - 0.01
        ask = price + 0.01
        volume += 20 + (i % 7)
        amount += (20 + (i % 7)) * 100.0 * price
        rows.append({
            "time": _epoch_ms(t), "lastPrice": price, "lastClose": base - 0.2,
            "volume": volume, "amount": amount,
            "bidPrice": [bid - 0.01*j for j in range(5)],
            "askPrice": [ask + 0.01*j for j in range(5)],
            "bidVol": [1000 + i % 50 + j*100 for j in range(5)],
            "askVol": [900 + (i*3) % 50 + j*100 for j in range(5)],
        })
        i += 1
        t += timedelta(seconds=1)
    return pd.DataFrame(rows)


def main() -> None:
    day = "2026-09-01"
    am = _raw_session(day, "09:30:00", "11:30:00", 30.0, 0.0002)
    pm = _raw_session(day, "13:00:00", "15:00:00", 30.2, -0.0001)
    raw = pd.concat([am, pm], ignore_index=True)
    norm = w._normalize_raw(raw, "301236.SZ")

    first = norm["_ts"].iloc[0]
    assert first.hour == 9 and first.minute == 30, f"UTC/CN conversion broken: {first}"
    assert norm["cum_high"].iloc[0] < norm["cum_high"].max(), "past-only cum_high unexpectedly sees future day high"

    gam = w._session_grid(norm[norm["trade_date"] == day], day, "AM", "09:30:00", "11:30:00")
    gpm = w._session_grid(norm[norm["trade_date"] == day], day, "PM", "13:00:00", "15:00:00")
    assert not gam.empty and not gpm.empty
    vam = gam[gam["valid"]]
    vpm = gpm[gpm["valid"]]
    assert not vam.empty and not vpm.empty
    assert vam["_ts"].min() >= pd.Timestamp(f"{day} 09:31:00"), "60s warmup missing"
    assert vam["_ts"].max() <= pd.Timestamp(f"{day} 11:28:55"), "label crosses lunch"
    assert vpm["_ts"].min() >= pd.Timestamp(f"{day} 13:01:00"), "PM warmup missing"
    assert vpm["_ts"].max() <= pd.Timestamp(f"{day} 14:58:55"), "label crosses close"

    r = vam.iloc[len(vam)//2]
    fx = w._features(r, 0.3, 0.5)
    banned = [k for k in fx if k.startswith(("ret_", "mid_60", "smoothed_mid", "future_", "label_"))]
    assert not banned, f"future feature leakage: {banned}"
    assert math.isfinite(float(r["ret_ask_to_bid_60_pct"])), "exact ask->future bid diagnostic missing"
    assert float(r["future_bid_60"]) > 0 and float(r["ask1"]) > 0

    print("PASS: QMT historical replay uses China time, session-isolated labels, past-only highs/lows, and leak-free features")


if __name__ == "__main__":
    main()
