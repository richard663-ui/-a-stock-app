import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pandas as pd
from modules.market_alpha import (normalize_daily, normalize_universe, daily_features,
                                 attach_forward_labels, rank_baseline, summarize_rank_ic)
from services.market_alpha_data import worker, coverage


class MarketAlphaTests(unittest.TestCase):
    def prices(self, code="SAMPLE.SH", n=160):
        dates = pd.bdate_range("2025-01-01", periods=n)
        close = 10 + np.arange(n) * 0.02
        return pd.DataFrame({"date": dates, "stock_code": code, "open": close-0.01,
                             "close": close, "high": close+0.02, "low": close-0.03,
                             "volume": 1e7, "amount": 1e8})

    def test_universe_not_limited_to_eight_and_exchanges_explicit(self):
        codes = [f"{i:06d}.SZ" for i in range(100)] + ["920001.BJ", "600000.SH", "bad", "600000.SH"]
        result = normalize_universe(codes)
        self.assertEqual(len(result), 102)
        self.assertIn("920001.BJ", result)

    def test_daily_normalization_rejects_invalid_ohlc_and_future(self):
        data = self.prices().set_index("date").tail(5)
        result = normalize_daily(data, "SAMPLE.SH", data.index[-2])
        self.assertEqual(len(result), 4)
        data.loc[data.index[0], "high"] = 1
        with self.assertRaises(ValueError):
            normalize_daily(data, "SAMPLE.SH", data.index[-1])

    def test_labels_start_next_open_and_recent_returns_remain_unknown(self):
        data = self.prices()
        features = daily_features(data, data["date"])
        output = attach_forward_labels(features, data, [5, 20], 20)
        expected = data["close"].iloc[5] / data["open"].iloc[1] - 1
        self.assertAlmostEqual(output["future_return_5d"].iloc[0], expected)
        self.assertAlmostEqual(output["future_net_excess_5d"].iloc[0], -0.002)
        self.assertTrue(output["future_return_20d"].tail(20).isna().all())

    def test_missing_trading_session_is_not_shifted_to_a_later_stock_row(self):
        prices = self.prices()
        incomplete = prices.drop(index=5)
        features = daily_features(incomplete, prices["date"])
        output = attach_forward_labels(features, prices, [5], 20)
        self.assertTrue(pd.isna(output["future_return_5d"].iloc[0]))
        self.assertFalse(output["has_future_5d"].iloc[0])

    def test_current_score_does_not_use_future_labels_or_prices(self):
        prices = self.prices()
        config = {"minimum_history": 120, "minimum_amount_20d": 2e7, "version": "test-only",
                  "baseline_weights": {"momentum_20d": .35, "momentum_60d": .35,
                                       "amount_20d": .15, "low_volatility_20d": .15}}
        features = daily_features(prices, prices["date"])
        before = rank_baseline(features, config)
        altered = prices.copy()
        altered.loc[151:, ["open", "close", "high", "low"]] *= 2
        after = rank_baseline(daily_features(altered, prices["date"]), config)
        pd.testing.assert_series_equal(before["baseline_rank_score"].iloc[:151], after["baseline_rank_score"].iloc[:151])
        self.assertFalse(before["active_for_signals"].any())
        labeled = attach_forward_labels(before, prices, [5], 20)
        summary = summarize_rank_ic(labeled, [5])
        self.assertTrue(summary["rank_ic"].isna().all(), "One stock must not produce a convincing IC.")

    def test_full_coverage_reports_missing_and_stale_stocks_without_excluding_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "daily").mkdir()
            self.prices().assign(stock_code="000001.SZ").to_csv(root / "daily" / "000001.SZ.csv", index=False)
            last = self.prices()["date"].iloc[-1].strftime("%Y-%m-%d")
            result = coverage(["000001.SZ", "000002.SZ"], root, last, 120, "000300.SH")
            self.assertEqual(len(result), 3)
            self.assertEqual(int(result["data_ready"].sum()), 1)
            self.assertEqual(result.loc[result["stock_code"] == "000002.SZ", "issue"].iloc[0], "missing_daily_cache")
            self.assertFalse(result["data_ready"].all())

    def test_partial_failure_preserves_old_cache_and_saves_successful_stock(self):
        data = self.prices().set_index("date")
        last = data.index[-1].strftime("%Y-%m-%d")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "daily").mkdir()
            old = root / "daily" / "000002.SZ.csv"
            self.prices().assign(stock_code="000002.SZ").to_csv(old, index=False)
            before = old.read_bytes()
            api = SimpleNamespace(get_market_data_ex=lambda *args, **kwargs: {"000001.SZ": data}, enable_hello=False)
            with patch.dict("sys.modules", {"xtquant": SimpleNamespace(xtdata=api)}):
                response = worker({"action": "daily", "symbols": ["000001.SZ", "000002.SZ"],
                    "download": False, "start_date": "20230101", "as_of": last, "data_root": directory})
            self.assertEqual(response["rows"][0]["status"], "UPDATED")
            self.assertEqual(response["rows"][1]["status"], "FAILED_CACHE_PRESERVED")
            self.assertEqual(old.read_bytes(), before)
            self.assertTrue((root / "daily" / "000001.SZ.csv").exists())


if __name__ == "__main__":
    unittest.main()
