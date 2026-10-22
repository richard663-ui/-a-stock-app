import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.market_clock import SHANGHAI  # noqa: E402
from services import qmt_tick_backfill as bf  # noqa: E402


class BackfillTests(unittest.TestCase):
    def test_store_is_separate_idempotent_and_round_trips(self):
        t0 = int(datetime(2026, 9, 9, 10, 0, tzinfo=SHANGHAI).timestamp() * 1000)
        frame = pd.DataFrame([{"time": t0 + 3000 * i, "lastPrice": 10 + 0.01 * i, "volume": 100 + i,
                               "amount": 1e6 + i, "lastClose": 9.9, "open": 10, "high": 10.5, "low": 9.8,
                               "bidPrice": [10 + 0.01 * i - 0.01] * 5, "askPrice": [10 + 0.01 * i] * 5,
                               "bidVol": [100] * 5, "askVol": [200] * 5} for i in range(5)])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(bf._store_frame(root, "600522.SH", frame), {"2026-09-09": 5})
            self.assertEqual(bf._store_frame(root, "600522.SH", frame), {"2026-09-09": 0})   # idempotent
            self.assertFalse((root / "raw").exists(), "backfill must never write into the live raw archive")
            back = bf.load_backfill_frame(root, "600522.SH", ["2026-09-09"])
            self.assertEqual(len(back), 5)
            self.assertEqual(back["bidPrice"].iloc[0][0], 9.99)

    def test_refuses_market_hours(self):
        self.assertTrue(bf.in_market_hours(datetime(2026, 10, 21, 10, 0, tzinfo=SHANGHAI)))
        self.assertFalse(bf.in_market_hours(datetime(2026, 10, 21, 16, 0, tzinfo=SHANGHAI)))
        self.assertFalse(bf.in_market_hours(datetime(2026, 10, 24, 10, 0, tzinfo=SHANGHAI)))


if __name__ == "__main__":
    unittest.main()
