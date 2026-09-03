# -*- coding: utf-8 -*-
"""V6 L2 recorder: preserve opening-auction data and tag morning regimes.

Opening auction (09:15-09:25) is recorded as a separate regime and is NOT meant
to be mixed blindly with continuous-auction training. Continuous 5-second
sampling and +60s smoothed-mid labels are unchanged.
"""
from __future__ import annotations

from datetime import datetime

import services.qmt_l2_training_recorder_v5 as v5

base = v5.base
RECORDER_VERSION = "l2-training-recorder-v6-morning-auction-20260903"
base.RECORDER_VERSION = RECORDER_VERSION

_regular_market_open = base._market_open
_regular_session = base._session
_base_feature_snapshot = base._feature_snapshot


def _market_open_with_auction(now=None) -> bool:
    d = now or datetime.now()
    if d.weekday() >= 5:
        return False
    m = d.hour * 60 + d.minute
    return (555 <= m < 565) or _regular_market_open(d)  # 09:15-09:25 + regular session


def _session_with_auction(now=None) -> str:
    d = now or datetime.now()
    m = d.hour * 60 + d.minute
    if 555 <= m < 565:
        return "OPEN_AUCTION"
    return _regular_session(d)


def _feature_snapshot(row, tick_rows, snap):
    features, meta = _base_feature_snapshot(row, tick_rows, snap)
    minute = float(features.get("minute_of_day") or 0.0)
    features.update({
        "phase_open_auction": int(555.0 <= minute < 565.0),
        "phase_open_core": int(570.0 <= minute < 630.0),
        "phase_am_late": int(630.0 <= minute < 690.0),
        "phase_pm": int(780.0 <= minute < 900.0),
    })
    return features, meta


base._market_open = _market_open_with_auction
base._session = _session_with_auction
base._feature_snapshot = _feature_snapshot


def main() -> None:
    print("AStock L2 training recorder V6 morning-priority mode")
    print(f"Recorder: {RECORDER_VERSION}")
    print("09:15-09:25 opening auction is recorded separately.")
    print("09:30-10:30 is tagged as the core morning regime.")
    print("Auction samples are for separate research and must not be mixed blindly with continuous trading.")
    base.main()


if __name__ == "__main__":
    main()
