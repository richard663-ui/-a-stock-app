import importlib.util
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ranking_study", ROOT / "research" / "tools" / "ranking_study.py")
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)


def panel(n=60, seed=0):
    rng = np.random.default_rng(seed)
    d = pd.Timestamp("2016-03-01")
    return pd.DataFrame({
        "date": d, "symbol": [f"{i:06d}.SZ" for i in range(n)], "traded": True, "bars": 500,
        "close_raw": 10.0, "amt20": np.linspace(3e7, 3e8, n), "is_st": False, "entry_ok": True,
        "R5": rng.normal(0, 0.05, n), "R20": rng.normal(0, 0.1, n),
        "vol_ratio": rng.uniform(0.5, 2, n), "logamt20": np.log(np.linspace(3e7, 3e8, n)),
        "vol20": rng.uniform(0.01, 0.05, n), "dist_high60": rng.uniform(-0.4, 0, n),
        "mom20": rng.normal(0, 0.1, n), "mom60_5": rng.normal(0, 0.2, n), "rev5": rng.normal(0, 0.05, n)})


class RankingStudyTests(unittest.TestCase):
    def test_capacity_filter_and_scores(self):
        f = rs.score_block(panel(), 5e7)
        self.assertTrue((f["amt20"] >= 5e7).all())
        for s in rs.STRATEGIES:
            self.assertTrue(f[s].between(0, 1).all(), s)
        # The low-volatility component ranks the calmest stock highest.
        calm = f.loc[f["vol20"].idxmin()]
        self.assertEqual(calm["S_lowvol"], f["S_lowvol"].max())

    def test_baseline_matches_market_alpha_definition(self):
        f = rs.score_block(panel(), 0)
        mom60 = (1 + f["mom60_5"]) * (1 + f["rev5"]) - 1
        expected = (0.35 * f["mom20"].rank(pct=True) + 0.35 * mom60.rank(pct=True)
                    + 0.15 * f["amt20"].rank(pct=True) + 0.15 * f["vol20"].rank(pct=True, ascending=False))
        self.assertTrue(np.allclose(f["B0"], expected))

    def test_top30_selection(self):
        f = rs.score_block(panel(n=80), 0)
        sel = rs.select(f, "C1")
        top = sel[sel["rule"] == "top30"]
        self.assertEqual(len(top), 30)
        self.assertGreaterEqual(f.set_index("symbol").loc[top["symbol"], "C1"].min(),
                                f.set_index("symbol").drop(top["symbol"])["C1"].max())

    def test_per_date_spearman_matches_pandas(self):
        rng = np.random.default_rng(3)
        dates = pd.Series(np.repeat(pd.to_datetime(["2016-01-04", "2016-01-05"]), 50))
        x, y = pd.Series(rng.normal(size=100)), pd.Series(rng.normal(size=100))
        y.iloc[3] = np.nan
        got = rs.per_date_spearman(dates, x, y)
        for d in dates.unique():
            m = (dates == d).to_numpy()
            self.assertAlmostEqual(got.loc[d], x[m].corr(y[m], method="spearman"), places=10)

    def test_turnover_costs_and_excess(self):
        d1, d2 = pd.Timestamp("2016-01-04"), pd.Timestamp("2016-01-11")
        sel = pd.DataFrame({"date": [d1, d1, d2, d2], "symbol": ["A", "B", "B", "C"],
                            "R5": [0.02, 0.04, 0.01, 0.03], "amt20": 1e8})
        bench = pd.Series([0.01, 0.0], index=[d1, d2])
        p = rs.portfolio_periods(sel, bench, 5, cost_bp=30)
        self.assertEqual(list(p["turnover"]), [1.0, 0.5])
        self.assertAlmostEqual(p["net"].iloc[0], 0.03 - 0.003)
        self.assertAlmostEqual(p["net"].iloc[1], 0.02 - 0.0015)
        self.assertAlmostEqual(p["excess"].iloc[1], 0.0185)


if __name__ == "__main__":
    unittest.main()
