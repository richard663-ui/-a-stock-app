import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.costs import CostModel, load_cost_model  # noqa: E402
from modules.market_clock import SHANGHAI  # noqa: E402


class CostTests(unittest.TestCase):
    def test_round_trip_costs_match_a_share_rules(self):
        model = CostModel()
        self.assertAlmostEqual(model.side_fee_bp("buy", 20000), 2.6, places=6)    # 2.5 commission + 0.1 transfer
        self.assertAlmostEqual(model.side_fee_bp("sell", 20000), 7.6, places=6)   # + 5 bp stamp duty
        self.assertAlmostEqual(model.round_trip_fees_bp(20000), 10.2, places=6)
        # The CNY 5 minimum dominates small tickets: 5000 CNY pays 10 bp commission per side.
        self.assertAlmostEqual(model.side_fee_bp("buy", 5000), 10.1, places=6)
        self.assertAlmostEqual(model.round_trip_bp(3.4), 13.6, places=6)
        self.assertAlmostEqual(model.round_trip_bp(3.4, include_spread=False), 10.2, places=6)
        self.assertEqual(len(model.costs_bp([0.034, None, float("nan")])), 3)

    def test_config_loads(self):
        self.assertEqual(load_cost_model().stamp_duty_sell_rate, 0.0005)


def minute_rows(day, symbols, minutes, start_hour=9, start_min=30):
    rows = []
    base = datetime.fromisoformat(f"{day}T{start_hour:02d}:{start_min:02d}:00").replace(tzinfo=SHANGHAI).timestamp()
    for symbol in symbols:
        for m in range(minutes):
            ts = base + 60 * m
            rows.append({"symbol": symbol, "trade_date": day, "generated_ts": ts})
    return rows


class SplitTests(unittest.TestCase):
    def test_low_coverage_days_cannot_be_validation_or_test(self):
        import services.train_l2_60s_model_v3 as split
        frame = pd.DataFrame(minute_rows("2026-09-02", ["A", "B"], 240) + minute_rows("2026-09-04", ["A", "B"], 240)
                             + minute_rows("2026-10-20", ["A", "B"], 2) + minute_rows("2026-10-21", ["A", "B"], 230))
        train, val, test, meta = split._split(frame)
        self.assertEqual(sorted(train["trade_date"].unique()), ["2026-09-02"])
        self.assertEqual(val["trade_date"].unique().tolist(), ["2026-09-04"])
        self.assertEqual(test["trade_date"].unique().tolist(), ["2026-10-21"])
        self.assertEqual(meta["days_not_used"], ["2026-10-20"])
        self.assertEqual(meta["maturity"], "STANDARD")

    def test_thin_evaluation_days_are_labelled_low_coverage(self):
        import services.train_l2_60s_model_v3 as split
        frame = pd.DataFrame(minute_rows("2026-09-02", ["A"], 240) + minute_rows("2026-10-19", ["A"], 2)
                             + minute_rows("2026-10-20", ["A"], 2))
        meta = split._split(frame)[3]
        self.assertEqual(meta["maturity"], "LOW_COVERAGE")

    def test_robust_split_uses_three_covered_days(self):
        import services.train_l1_60s_model_v5_challenger as v5
        frame = pd.DataFrame(minute_rows("2026-09-01", ["A"], 240) + minute_rows("2026-09-02", ["A"], 240)
                             + minute_rows("2026-09-03", ["A"], 3) + minute_rows("2026-09-04", ["A"], 240)
                             + minute_rows("2026-09-07", ["A"], 240))
        train, val, test, meta = v5._robust_split(frame)
        self.assertEqual(sorted(val["trade_date"].unique()), ["2026-09-02", "2026-09-04"])
        self.assertEqual(test["trade_date"].unique().tolist(), ["2026-09-07"])
        self.assertIn("2026-09-03", meta["days_not_used"])


class FeatureTests(unittest.TestCase):
    def test_five_second_deltas_are_not_computed_across_gaps(self):
        import services.train_l1_60s_model_v3 as v3
        base = datetime(2026, 10, 21, 10, 0, tzinfo=SHANGHAI).timestamp()
        rows = []
        for i, offset in enumerate([0, 5, 10, 600, 605]):
            ts = base + offset
            rows.append({"symbol": "A", "sample_bucket": int(ts // 5 * 5), "generated_ts": ts,
                         "generated_at": datetime.fromtimestamp(ts, SHANGHAI).isoformat(),
                         "label_threshold_pct": 0.01, "ret_smoothed_mid_60_pct": 0.0,
                         "features_json": json.dumps({"volume": 1000 + 10 * i * i, "amount": 1e6, "spread_pct": 0.03,
                                                      "minute_of_day": 600})})
        out = v3._expand(pd.DataFrame(rows))
        delta = out["volume_delta_5s"].tolist()
        self.assertTrue(np.isnan(delta[0]))
        self.assertEqual(delta[1], 10.0)
        self.assertEqual(delta[2], 30.0)
        self.assertTrue(np.isnan(delta[3]), "change across a 590-second gap must be unknown")
        self.assertEqual(delta[4], 70.0)
        accel = out["volume_accel_5s"].tolist()
        self.assertEqual(accel[2], 20.0)
        self.assertTrue(np.isnan(accel[4]))

    def test_unparseable_exchange_time_is_not_replaced_by_local_clock(self):
        from modules.qmt_live import normalize_tick
        self.assertEqual(normalize_tick("A.SH", {"time": "garbage", "lastPrice": 10})["captured_at"], "")
        self.assertTrue(normalize_tick("A.SH", {"time": 1792706917000, "lastPrice": 10})["captured_at"].startswith("2026-"))

    def test_v17_session_end_is_exclusive(self):
        from modules.short_direction import _market_open
        at = lambda h, m: datetime(2026, 10, 21, h, m, 30, tzinfo=SHANGHAI)
        self.assertTrue(_market_open(at(11, 29)))
        self.assertFalse(_market_open(at(11, 30)))
        self.assertFalse(_market_open(at(15, 0)))


class LabelTests(unittest.TestCase):
    def test_v18_explains_that_l1_mode_never_emits_direction(self):
        from modules.direction_v18 import analyze_direction_v18
        now = datetime.now(SHANGHAI).timestamp()
        ticks = pd.DataFrame([{"captured_at": datetime.fromtimestamp(now - 90 + 3 * i, SHANGHAI).isoformat(),
                               "lastPrice": 10 + 0.01 * i, "volume": 1000 + 50 * i, "amount": 1e6 + 5e4 * i,
                               "bidPrice": [10 + 0.01 * i - 0.01], "askPrice": [10 + 0.01 * i],
                               "bidVol": [500], "askVol": [100], "high": 11, "low": 9} for i in range(31)])
        out = analyze_direction_v18(ticks, {})
        self.assertEqual(out.get("direction_60", "WATCH"), "WATCH")
        if out.get("metrics", {}).get("safety_gate_reasons_60"):
            self.assertIn("需真实L2", out["label_60"])

    def test_position_suggestions_are_marked_experimental(self):
        from modules.signals import make_action
        qishi = {"ok": True, "latest_score": 90, "latest_state": "x", "fund_score": 70,
                 "consecutive_red": 2, "risk_state": "风险可控", "fund_label": "放量上涨", "df": pd.DataFrame()}
        action = make_action({"price": 10}, qishi, {"score": 50}, {"score": 80}, False, 0, 0)
        self.assertIn("实验规则", action["buy_label"])
        self.assertIn("未验证", action["new_pos"])


class RealisticCostReportTests(unittest.TestCase):
    def test_head_metrics_report_realistic_net_and_readiness_requires_it(self):
        import services.train_l1_60s_model_v4 as core
        frame = pd.DataFrame({core.RET_TARGET: [0.05, 0.05, -0.02], "spread_pct": [0.03, 0.03, 0.03],
                              core.UP_TARGET: [1, 1, 0]})
        m = core._head_metrics(frame, np.array([0.9, 0.9, 0.1]), 0.5, 1, 2.0, core.UP_TARGET)
        self.assertAlmostEqual(m["avg_gross_edge_bp"], 5.0)
        self.assertAlmostEqual(m["avg_net_edge_bp"], 3.0)                    # legacy 2 bp hurdle
        self.assertAlmostEqual(m["avg_net_edge_realistic_bp"], 5.0 - 13.2, places=6)
        ready = core._readiness({"maturity": "STANDARD"}, {**m, "directional_predictions": 100,
                                                           "directional_accuracy_pct": 60}, {})
        self.assertIn("realistic_cost_net_edge_not_positive", ready["reasons"])


if __name__ == "__main__":
    unittest.main()
