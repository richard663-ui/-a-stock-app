import importlib.util
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_research_daily", ROOT / "research" / "tools" / "build_research_daily.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ResearchDailyDateTests(unittest.TestCase):
    def test_monday_bar_is_dated_monday_not_local_sunday(self):
        # 2026-10-12 (Monday) 00:00 Shanghai; on a UTC+1 PC xtdata labels it "20261011" (Sunday).
        monday = int(pd.Timestamp("2026-10-12 00:00", tz="Asia/Shanghai").timestamp() * 1000)
        raw = pd.DataFrame({"time": [monday], "open": [10.0], "high": [10.5], "low": [9.9], "close": [10.2],
                            "volume": [1000.0], "amount": [1.02e6], "preClose": [10.0], "suspendFlag": [0]},
                           index=["20261011"])
        adj = raw[["open", "high", "low", "close"]]
        frame = builder._frame({"600000.SH": raw}, {"600000.SH": adj})
        self.assertEqual(frame["date"].iloc[0], pd.Timestamp("2026-10-12"))
        self.assertEqual(frame["volume_shares"].iloc[0], 100000.0)

    def test_bars_without_epoch_time_are_rejected(self):
        raw = pd.DataFrame({"close": [1.0]}, index=["20261011"])
        with self.assertRaises(ValueError):
            builder.bar_dates(raw)


if __name__ == "__main__":
    unittest.main()
