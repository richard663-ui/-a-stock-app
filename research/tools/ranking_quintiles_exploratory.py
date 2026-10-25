"""EXPLORATORY (post-hoc, not a test): where in the C1 ranking does the information sit?

Every 20th session, universe >= 50m CNY 20-day amount: mean 20-day excess return (vs the
equal-weight universe, gross of costs) of each C1 quintile, and of "universe minus the
bottom quintile". All history has been seen, so these numbers only shape the next
pre-registered, forward-tested hypothesis. Writes research/results/ranking_quintiles_exploratory.json.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

_spec = importlib.util.spec_from_file_location("ranking_study", Path(__file__).with_name("ranking_study.py"))
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)
mds = rs.mds


def main() -> dict:
    dataset = ds.dataset(str(mds.FEAT), format="parquet")
    calendar = mds.load_calendar()
    dates20 = set(calendar[::5][::4])
    rows = []
    for year in range(2015, calendar[-1].year + 1):
        filt = (ds.field("date") >= pd.Timestamp(f"{year}-01-01")) & (ds.field("date") <= pd.Timestamp(f"{year}-12-31"))
        frame = dataset.to_table(columns=rs.COLS, filter=filt).to_pandas()
        frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
        frame = frame[frame["date"].isin(dates20)]
        f = rs.score_block(frame, rs.UNIVERSES["U50"]).dropna(subset=["R20"])
        f["ex"] = f["R20"] - f.groupby("date")["R20"].transform("mean")
        f["q"] = f.groupby("date")["C1"].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False)) + 1
        for date, g in f.groupby("date"):
            rec = {"date": date}
            for q in range(1, 6):
                rec[f"Q{q}"] = float(g.loc[g["q"] == q, "ex"].mean())
            rec["ex_bottom"] = float(g.loc[g["q"] > 1, "ex"].mean())
            rows.append(rec)
    p = pd.DataFrame(rows)
    out = {"note": "Exploratory, post-hoc, gross of costs; not evidence for a strategy."}
    for name, lo, hi in (("2015-2021", "2015-01-01", "2021-12-31"), ("2022-2026", "2022-01-01", "2026-12-31")):
        part = p[(p["date"] >= lo) & (p["date"] <= hi)]
        out[name] = {c: {"ann_excess_pct": round(float(part[c].mean()) * 250 / 20 * 100, 2),
                         "nw_t": round(mds.nw_t(part[c].to_numpy(), 2), 2)}
                     for c in ["Q1", "Q2", "Q3", "Q4", "Q5", "ex_bottom"]}
    path = rs.RESULTS / "ranking_quintiles_exploratory.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
