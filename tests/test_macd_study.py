import importlib.util
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("macd_daily_study", ROOT / "research" / "tools" / "macd_daily_study.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def bars(n=300, seed=1, symbol="600000.SH"):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2016-01-04", periods=n)
    close = 10 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    openp = close * (1 + rng.normal(0, 0.005, n))
    frame = pd.DataFrame({"date": dates, "symbol": symbol, "open": openp, "high": np.maximum(openp, close) * 1.01,
                          "low": np.minimum(openp, close) * 0.99, "close": close, "close_raw": close,
                          "pre_close_raw": np.r_[close[0], close[:-1]], "volume_shares": 1e6,
                          "amount_cny": 1e6 * close, "suspended": False})
    return frame, pd.DatetimeIndex(dates)


class MacdStudyTests(unittest.TestCase):
    def test_features_do_not_look_ahead(self):
        frame, cal = bars()
        base = study.stock_features("600000.SH", frame, cal)
        changed = frame.copy()
        changed.loc[250:, ["open", "close", "close_raw"]] *= 1.5
        alt = study.stock_features("600000.SH", changed, cal)
        cols = study.MACD_FEATURES + study.CONTROLS + [e for e in study.EVENTS if e != "ALL"]
        for col in cols:
            a, b = base[col].iloc[:250].to_numpy(), alt[col].iloc[:250].to_numpy()
            self.assertTrue(np.allclose(a.astype(float), b.astype(float), equal_nan=True), col)

    def test_forward_return_enters_next_open(self):
        frame, cal = bars()
        f = study.stock_features("600000.SH", frame, cal)
        t = 150
        expected = frame["close"].iloc[t + 5] / frame["open"].iloc[t + 1] - 1
        self.assertAlmostEqual(float(f["R5"].iloc[t]), expected, places=5)
        self.assertTrue(np.isnan(f["R20"].iloc[-1]))          # sample end: missing, not filled

    def test_golden_cross_definition(self):
        frame, cal = bars(seed=3)
        f = study.stock_features("600000.SH", frame, cal)
        hist = f["hist_n"].to_numpy()
        gc = (f["GC_above"] | f["GC_below"]).to_numpy()
        expected = np.r_[False, (hist[1:] > 0) & (hist[:-1] <= 0)]
        self.assertTrue((gc == expected).all())
        self.assertGreater(gc.sum(), 3)

    def test_limit_up_open_blocks_entry(self):
        frame, cal = bars()
        frame.loc[201, "open"] = frame.loc[201, "pre_close_raw"] * 1.10
        f = study.stock_features("600000.SH", frame, cal)
        self.assertFalse(bool(f["entry_ok"].iloc[200]))
        self.assertTrue(bool(f["entry_ok"].iloc[199]))

    def test_delisted_exit_uses_last_close(self):
        frame, _ = bars(n=200)
        cal = pd.bdate_range("2016-01-04", periods=260)
        f = study.stock_features("600001.SH", frame, pd.DatetimeIndex(cal), delisted=True)
        t = 195
        expected = frame["close"].iloc[-1] / frame["open"].iloc[t + 1] - 1
        self.assertAlmostEqual(float(f["R20"].iloc[t]), expected, places=5)

    def test_newey_west_t(self):
        rng = np.random.default_rng(0)
        self.assertLess(abs(study.nw_t(rng.normal(0, 1, 2000), 5)), 3)
        self.assertGreater(study.nw_t(rng.normal(0.5, 1, 2000), 5), 10)


if __name__ == "__main__":
    unittest.main()
