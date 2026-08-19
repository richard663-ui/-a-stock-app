# -*- coding: utf-8 -*-
"""Deterministic offline self-test for V18 Final.

No network, no QMT and no Supabase are required. This catches syntax/interface
regressions before the user touches the Windows deployment.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.direction_v18 import analyze_direction_v18
from modules.level2_engine import analyze_level2
from modules.prediction_journal import PredictionJournal
from modules.setup_vwap import analyze_vwap_state, grade_setup


def _ticks() -> pd.DataFrame:
    now = datetime.now()
    rows = []
    volume = 100000.0
    amount = 22.0 * volume * 100.0
    for i in range(80):
        price = 22.0 + i * 0.001
        volume += 20 + i % 5
        amount += price * (20 + i % 5) * 100.0
        rows.append({
            "captured_at": (now - timedelta(seconds=79-i)).isoformat(),
            "lastPrice": price,
            "lastClose": 21.9,
            "volume": volume,
            "amount": amount,
            "high": price + .02,
            "low": price - .02,
            "bidPrice": [price-.01, price-.02, price-.03, price-.04, price-.05],
            "askPrice": [price+.01, price+.02, price+.03, price+.04, price+.05],
            "bidVol": [800, 700, 600, 500, 400],
            "askVol": [500, 450, 400, 350, 300],
        })
    return pd.DataFrame(rows)


def main() -> int:
    ticks = _ticks()
    vwap = analyze_vwap_state(ticks)
    assert vwap["ok"] and vwap["vwap"] > 0

    l2 = analyze_level2(
        quotes=[
            {"time": 1700000002000,
             "bidVol": [900, 850, 800, 750, 700, 650, 600, 550, 500, 450],
             "askVol": [300, 320, 340, 360, 380, 400, 420, 440, 460, 480]},
        ],
        transactions=[
            {"time": 1700000000000, "tradeFlag": 1, "price": 22.0, "volume": 1000, "amount": 2_200_000},
            {"time": 1700000001000, "tradeFlag": 1, "price": 22.01, "volume": 900, "amount": 1_980_900},
            {"time": 1700000002000, "tradeFlag": 2, "price": 22.0, "volume": 200, "amount": 440_000},
        ],
        orders=[
            {"time": 1700000000000, "entrustDirection": 1, "volume": 1000},
            {"time": 1700000001000, "entrustDirection": 1, "volume": 800},
            {"time": 1700000002000, "entrustDirection": 2, "volume": 300},
        ],
        quoteaux=[
            {"time": 1700000000000, "withdrawBidAmount": 100, "withdrawOffAmount": 100, "totalBidQuantity": 10000, "totalOffQuantity": 7000},
            {"time": 1700000002000, "withdrawBidAmount": 150, "withdrawOffAmount": 400, "totalBidQuantity": 12000, "totalOffQuantity": 6500},
        ],
        transactioncount=[
            {"time": 1700000002000, "bidMostAmount": 1_200_000, "bidBigAmount": 800_000,
             "bidMediumAmount": 300_000, "bidSmallAmount": 100_000,
             "offMostAmount": 300_000, "offBigAmount": 200_000,
             "offMediumAmount": 100_000, "offSmallAmount": 50_000},
        ],
        orderqueue=[
            {"time": 1700000002000, "bidLevelVolume": [600, 300, 100],
             "offerLevelVolume": [100, 100, 100]},
        ],
    )
    assert l2["ok"]
    assert l2["available"]["l2quote"] is True
    assert l2["metrics"]["depth10_buy_pct"] > 50
    assert l2["metrics"]["queue_bid_volume"] == 1000
    assert l2["metrics"]["queue_offer_volume"] == 300
    assert l2["metrics"]["big_buy_pct"] > 50

    direction = analyze_direction_v18(ticks, l2)
    assert "direction_60" in direction and "condition_agreement" in direction

    setup = grade_setup(
        qishi={"ok": True, "latest_score": 75, "risk_state": "风险可控"},
        macd={"label": "刚形成金叉"},
        catalyst={"score": 40},
        vwap=vwap,
        short_metrics=direction.get("metrics", {}),
        l2_summary=l2,
    )
    assert setup["grade"] in {"A", "B", "C", "D"} and 0 <= setup["score"] <= 100

    with tempfile.TemporaryDirectory() as td:
        journal = PredictionJournal(Path(td) / "pred.sqlite3")
        journal.record(symbol="000400.SZ", price=22.0, direction="UP", agreement=95,
                       high_confidence=True, true_l2=True, features={"x": 1}, now_ts=1000.0)
        journal.mature(symbol="000400.SZ", current_price=22.1, now_ts=1061.0)
        stats = journal.stats("000400.SZ")
        assert stats["true_l2_high_conf_samples"] == 1
        assert stats["true_l2_high_conf_accuracy_pct"] == 100.0

        journal.record(symbol="600406.SH", price=20.0, direction="UP", agreement=95,
                       high_confidence=True, true_l2=True, features={}, now_ts=2000.0)
        journal.mature(symbol="600406.SH", current_price=22.0, now_ts=2200.0)
        stale_stats = journal.stats("600406.SH")
        assert stale_stats["all_samples"] == 0
        assert stale_stats["expired_samples"] == 1

    print("[PASS] V18 setup/VWAP engine")
    print("[PASS] V18 Level-2 engine incl. 10-level depth + queue arrays")
    print("[PASS] V18 direction interface")
    print("[PASS] V18 prediction journal incl. outage expiry")
    print("RESULT: SELFTEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
