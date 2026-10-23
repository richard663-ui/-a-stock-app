"""Re-check the old 10-stock MACD event study (111 above-zero golden crosses, 57.7% next-day up).

Same stocks and definition (next close > cross-day close), compared with the same
stocks' unconditional up-day rate, on QMT data, split into the study's periods.
Writes research/results/macd_old_study_check.json.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parents[2]
DATA = Path.home() / "AStockData" / "research_daily"
STOCKS = ["000400.SZ", "600406.SH", "601179.SH", "600522.SH", "300274.SZ", "300750.SZ",
          "002371.SZ", "600519.SH", "600036.SH", "000333.SZ"]


def binom_p(k: int, n: int, p: float) -> float:
    """Two-sided normal-approximation p-value for k successes in n trials at rate p."""
    if n == 0:
        return float("nan")
    z = (k - n * p) / math.sqrt(n * p * (1 - p))
    return math.erfc(abs(z) / math.sqrt(2))


def main() -> dict:
    bars = ds.dataset(str(DATA / "bars_qmt"), format="parquet").to_table(
        columns=["date", "symbol", "close", "volume_shares"], filter=ds.field("symbol").isin(STOCKS)).to_pandas()
    out = {"stocks": STOCKS, "periods": {}}
    for name, lo, hi in (("2015-2021", "2015-01-01", "2021-12-31"), ("2022-2026", "2022-01-01", "2026-12-31"),
                         ("old_study_window_2022-08_to_2026-08", "2022-08-01", "2026-08-19")):
        cross_up = cross_n = base_up = base_n = 0
        for sym, g in bars.groupby("symbol"):
            g = g[g["volume_shares"] > 0].sort_values("date").reset_index(drop=True)
            c = g["close"].astype(float)
            dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
            dea = dif.ewm(span=9, adjust=False).mean()
            hist = dif - dea
            # The old study's "水上金叉": DIF and DEA both above zero at the cross.
            gc = (hist > 0) & (hist.shift(1) <= 0) & (dif > 0) & (dea > 0)
            up_next = c.shift(-1) > c
            inside = (g["date"] >= lo) & (g["date"] <= hi) & c.shift(-1).notna() & (g.index >= 60)
            cross_n += int((gc & inside).sum())
            cross_up += int((gc & inside & up_next).sum())
            base_n += int(inside.sum())
            base_up += int((inside & up_next).sum())
        rate, base = cross_up / max(cross_n, 1), base_up / max(base_n, 1)
        out["periods"][name] = {"golden_crosses_above_zero": cross_n, "next_day_up_rate": round(rate, 4),
                                "same_stocks_all_days_up_rate": round(base, 4),
                                "p_value_vs_own_base_rate": round(binom_p(cross_up, cross_n, base), 4)}
    path = ROOT / "research" / "results" / "macd_old_study_check.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
