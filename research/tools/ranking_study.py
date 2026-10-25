"""Replacement research ranking study (pre-registered: research/preregistration/ranking_composite_v1.md).

  python research/tools/ranking_study.py cross                 # selections + ICs per half-year block
  python research/tools/ranking_study.py report --period dev   # 2015-2021
  python research/tools/ranking_study.py report --period seen  # 2022-latest (components chosen with it in view)
  python research/tools/ranking_study.py forward               # dated target list for the paper track

Uses the MACD study's feature files (same data, eligibility and forward returns).
Research only: no production configuration or live signal is read or changed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("macd_daily_study", Path(__file__).with_name("macd_daily_study.py"))
mds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mds)

DATA = mds.DATA
OUT = DATA / "ranking_cross"
RESULTS = ROOT / "research" / "results"
PAPER = ROOT / "research" / "paper"
VERSION = "ranking-composite-v1"
PERIODS = {"dev": ("2015-01-01", "2021-12-31"), "seen": ("2022-01-01", "2099-12-31")}
UNIVERSES = {"U50": 5e7, "U20": 2e7}
TOP_N, COST_BP, REBALANCE = 30, 30.0, (5, 20)

# Component name -> (feature column, sign); higher signed value = more attractive.
COMPONENTS = {"S_lowabvol": ("vol_ratio", -1), "S_small": ("logamt20", -1), "S_lowvol": ("vol20", -1),
              "S_nearhigh": ("dist_high60", +1), "S_weak20": ("mom20", -1)}
COMPOSITES = {
    "C1": list(COMPONENTS),
    "C2": ["S_lowabvol", "S_small", "S_lowvol"],
    "C1_nosize": ["S_lowabvol", "S_lowvol", "S_nearhigh", "S_weak20"],
}
STRATEGIES = list(COMPOSITES) + ["B0"] + list(COMPONENTS)
COLS = ["date", "symbol", "traded", "bars", "close_raw", "amt20", "is_st", "entry_ok", "R5", "R20",
        "vol_ratio", "logamt20", "vol20", "dist_high60", "mom20", "mom60_5", "rev5"]


def score_block(frame: pd.DataFrame, min_amount: float) -> pd.DataFrame:
    """Eligible rows with a percentile score per strategy (higher = buy)."""
    elig = (frame["traded"] & (frame["bars"] >= mds.MIN_BARS) & (frame["close_raw"] >= mds.MIN_PRICE)
            & (frame["amt20"] >= min_amount) & ~frame["is_st"] & frame["entry_ok"])
    f = frame[elig].copy()
    f["mom60"] = (1 + f["mom60_5"]) * (1 + f["rev5"]) - 1
    f = f.dropna(subset=[c for c, _ in COMPONENTS.values()] + ["mom60"])
    g = f.groupby("date")
    for name, (col, sign) in COMPONENTS.items():
        f[name] = g[col].rank(pct=True, ascending=(sign > 0))
    for name, parts in COMPOSITES.items():
        f[name] = f[parts].mean(axis=1)
    # Current baseline, as in modules/market_alpha.py rank_baseline().
    f["B0"] = (0.35 * g["mom20"].rank(pct=True) + 0.35 * f.groupby("date")["mom60"].rank(pct=True)
               + 0.15 * g["amt20"].rank(pct=True) + 0.15 * g["vol20"].rank(pct=True, ascending=False))
    return f


def per_date_spearman(dates: pd.Series, x: pd.Series, y: pd.Series) -> pd.Series:
    """Spearman correlation of x and y within each date (Pearson on within-date ranks)."""
    df = pd.DataFrame({"d": dates.to_numpy(), "x": x.to_numpy(), "y": y.to_numpy()}).dropna()
    df["x"] = df.groupby("d")["x"].rank()
    df["y"] = df.groupby("d")["y"].rank()
    g = df.groupby("d")
    dx, dy = df["x"] - g["x"].transform("mean"), df["y"] - g["y"].transform("mean")
    sums = pd.DataFrame({"d": df["d"], "xy": dx * dy, "xx": dx * dx, "yy": dy * dy}).groupby("d").sum()
    return sums["xy"] / np.sqrt(sums["xx"] * sums["yy"])


def select(f: pd.DataFrame, strategy: str) -> pd.DataFrame:
    """Top-N and top-decile memberships for one strategy on every date in f."""
    pos = f.groupby("date")[strategy].rank(ascending=False, method="first")
    pct = f.groupby("date")[strategy].rank(pct=True)
    out = []
    for rule, mask in (("top30", pos <= TOP_N), ("decile", pct > 0.9)):
        part = f.loc[mask, ["date", "symbol", "R5", "R20", "amt20"]].copy()
        part["strategy"], part["rule"] = strategy, rule
        out.append(part)
    return pd.concat(out, ignore_index=True)


def run_cross() -> dict:
    dataset = ds.dataset(str(mds.FEAT), format="parquet")
    calendar = mds.load_calendar()
    rebalance_dates = set(calendar[::REBALANCE[0]])     # every 5th session; every 4th of these for 20 days
    OUT.mkdir(parents=True, exist_ok=True)
    blocks = []
    for year in range(calendar[0].year, calendar[-1].year + 1):
        for lo, hi in ((f"{year}-01-01", f"{year}-06-30"), (f"{year}-07-01", f"{year}-12-31")):
            filt = (ds.field("date") >= pd.Timestamp(lo)) & (ds.field("date") <= pd.Timestamp(hi))
            frame = dataset.to_table(columns=COLS, filter=filt).to_pandas()
            if frame.empty:
                continue
            frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
            sel_parts, ic_rows, bench_rows = [], [], []
            for uni, min_amount in UNIVERSES.items():
                f = score_block(frame, min_amount)
                for h in (5, 20):
                    ex = f[f"R{h}"] - f.groupby("date")[f"R{h}"].transform("mean")
                    for s in STRATEGIES:
                        per_date = per_date_spearman(f["date"], f[s], ex)
                        ic_rows.append(pd.DataFrame({"date": per_date.index, "universe": uni, "h": h,
                                                     "strategy": s, "ic": per_date.to_numpy()}))
                reb = f[f["date"].isin(rebalance_dates)]
                for s in STRATEGIES:
                    part = select(reb, s)
                    part["universe"] = uni
                    sel_parts.append(part)
                bench = reb.groupby("date").agg(R5=("R5", "mean"), R20=("R20", "mean"), n=("R5", "size")).reset_index()
                bench["universe"] = uni
                bench_rows.append(bench)
            pd.concat(sel_parts, ignore_index=True).to_parquet(OUT / f"selections_{lo[:7]}.parquet", index=False)
            pd.concat(ic_rows, ignore_index=True).to_parquet(OUT / f"ic_{lo[:7]}.parquet", index=False)
            pd.concat(bench_rows, ignore_index=True).to_parquet(OUT / f"bench_{lo[:7]}.parquet", index=False)
            blocks.append(lo[:7])
            print(f"ranking cross {lo}..{hi}: rows={len(frame)}", flush=True)
            del frame
    return {"blocks": blocks}


def portfolio_periods(sel: pd.DataFrame, bench: pd.Series, h: int, cost_bp: float = COST_BP) -> pd.DataFrame:
    """Per rebalance date: gross, turnover, net and excess return of an equal-weight basket.

    sel: rows (date, symbol, R<h>) for consecutive rebalance dates of one strategy/rule/universe.
    bench: equal-weight universe R<h> indexed by date. Turnover is the fraction of the new basket
    not held before (first basket: 1.0); each replaced name costs one round trip.
    """
    rows, prev = [], set()
    for date, g in sel.sort_values("date").groupby("date", sort=True):
        names = set(g["symbol"])
        ret = g[f"R{h}"].dropna()
        if ret.empty or date not in bench.index or not np.isfinite(bench.loc[date]):
            prev = names
            continue
        turnover = 1.0 if not prev else len(names - prev) / len(names)
        gross = float(ret.mean())
        net = gross - turnover * cost_bp / 1e4
        rows.append({"date": date, "gross": gross, "turnover": turnover, "net": net,
                     "bench": float(bench.loc[date]), "excess": net - float(bench.loc[date]),
                     "n": len(names), "median_amt20": float(g["amt20"].median())})
        prev = names
    return pd.DataFrame(rows)


def _drawdown(returns: pd.Series) -> float:
    wealth = (1 + returns.fillna(0)).cumprod()
    return float((wealth / wealth.cummax() - 1).min()) if len(wealth) else float("nan")


def summarise(p: pd.DataFrame, h: int, regimes: pd.DataFrame) -> dict:
    per_year = 250 / h
    ex = p["excess"].to_numpy()
    out = {"periods": int(len(p)),
           "ann_net_excess_pct": round(float(np.mean(ex)) * per_year * 100, 2),
           "ann_gross_excess_pct": round(float((p["gross"] - p["bench"]).mean()) * per_year * 100, 2),
           "nw_t": round(mds.nw_t(ex, 4), 2),
           "information_ratio": round(float(np.mean(ex) / np.std(ex) * np.sqrt(per_year)), 2) if np.std(ex) > 0 else None,
           "ann_net_return_pct": round(float(p["net"].mean()) * per_year * 100, 2),
           "ann_bench_return_pct": round(float(p["bench"].mean()) * per_year * 100, 2),
           "max_drawdown_pct": round(_drawdown(p["net"]) * 100, 1),
           "max_excess_drawdown_pct": round(_drawdown(p["excess"]) * 100, 1),
           "avg_turnover": round(float(p["turnover"].mean()), 3),
           "median_holding_amount_m": round(float(p["median_amt20"].median()) / 1e6, 1)}
    years = p.groupby(p["date"].dt.year)["excess"].mean() * per_year * 100
    out["by_year_excess_pct"] = {int(y): round(float(v), 1) for y, v in years.items()}
    out["years_positive"] = f"{int((years > 0).sum())}/{len(years)}"
    m = p.merge(regimes, left_on="date", right_index=True, how="left")
    for col in ("trend", "vol"):
        out[f"by_{col}_excess_pct"] = {k: round(float(g["excess"].mean()) * per_year * 100, 2)
                                       for k, g in m.groupby(col) if len(g) >= 10}
    return out


def _read(kind: str, lo: str, hi: str) -> pd.DataFrame:
    df = pd.concat([pd.read_parquet(p) for p in sorted(OUT.glob(f"{kind}_*.parquet"))], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
    return df[(df["date"] >= pd.Timestamp(lo)) & (df["date"] <= pd.Timestamp(hi))]


def index_returns(h: int) -> pd.Series:
    """CSI 1000: open of the next session to close h sessions later, by signal date."""
    idx = pd.read_parquet(DATA / "index.parquet")
    idx = idx[(idx["symbol"] == "000852.SH") & (idx["volume_shares"] > 0)].sort_values("date")
    idx["date"] = pd.to_datetime(idx["date"]).astype("datetime64[ns]")
    s = idx.set_index("date")
    return (s["close"].shift(-h) / s["open"].shift(-1) - 1).rename(f"csi1000_R{h}")


def run_report(period: str) -> dict:
    lo, hi = PERIODS[period]
    if period == "seen" and not (RESULTS / "ranking_composite_dev.json").exists():
        raise SystemExit("Development result must be written first.")
    sel, bench, ic = _read("selections", lo, hi), _read("bench", lo, hi), _read("ic", lo, hi)
    regimes = mds.regimes()
    calendar = mds.load_calendar()
    every20 = set(calendar[::REBALANCE[0]][::REBALANCE[1] // REBALANCE[0]])
    out = {"version": VERSION, "period": period, "range": [lo, hi], "cost_bp_round_trip": COST_BP,
           "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "portfolios": {}, "ic": {}, "csi1000": {}}
    for h in REBALANCE:
        csi = index_returns(h)
        for uni in UNIVERSES:
            b = bench[bench["universe"] == uni].set_index("date")[f"R{h}"]
            dates = b.index if h == 5 else b.index[b.index.isin(every20)]
            b = b.loc[dates]
            c = csi.reindex(dates).dropna()
            out["csi1000"][f"{uni}|h{h}"] = {"ann_ew_minus_csi1000_pct":
                                             round(float((b.reindex(c.index) - c).mean()) * 250 / h * 100, 2)}
            for strat in STRATEGIES:
                for rule in ("top30", "decile"):
                    part = sel[(sel["universe"] == uni) & (sel["strategy"] == strat) & (sel["rule"] == rule)
                               & sel["date"].isin(dates)]
                    p = portfolio_periods(part, b, h)
                    if p.empty:
                        continue
                    s = summarise(p, h, regimes)
                    cc = csi.reindex(p["date"])
                    s["ann_net_minus_csi1000_pct"] = round(float((p["net"].to_numpy() - cc.to_numpy())[
                        np.isfinite(cc.to_numpy())].mean()) * 250 / h * 100, 2)
                    out["portfolios"][f"{strat}|{rule}|{uni}|h{h}"] = s
    for (strat, uni, h), g in ic.groupby(["strategy", "universe", "h"]):
        g = g.sort_values("date")
        out["ic"][f"{strat}|{uni}|h{h}"] = {"mean_ic": round(float(g["ic"].mean()), 4),
                                           "nw_t": round(mds.nw_t(g["ic"].to_numpy(), h), 2),
                                           "positive_share": round(float((g["ic"] > 0).mean()), 3)}
    prim = out["portfolios"].get("C1|top30|U50|h5", {})
    base = out["portfolios"].get("B0|top30|U50|h5", {})
    if period == "dev":
        yrs = int(prim.get("years_positive", "0/0").split("/")[0])
        verdict = (prim.get("ann_net_excess_pct", -1) > 0 and prim.get("nw_t", 0) >= 2.5
                   and (prim.get("information_ratio") or -9) > (base.get("information_ratio") or -9) and yrs >= 5)
    else:
        verdict = prim.get("ann_net_excess_pct", -1) > 0 and prim.get("nw_t", 0) >= 2.0
    out["verdict"] = {"primary": "C1|top30|U50|h5", "criteria_met": bool(verdict),
                      "primary_summary": {k: prim.get(k) for k in ("ann_net_excess_pct", "nw_t", "information_ratio",
                                                                  "years_positive", "avg_turnover")},
                      "baseline_summary": {k: base.get(k) for k in ("ann_net_excess_pct", "nw_t", "information_ratio")}}
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"ranking_composite_{period}.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return {"written": str(path), **out["verdict"]}


def run_forward() -> dict:
    """Dated top-30 C1 list (and B0 for comparison) from the latest complete session; no orders."""
    dataset = ds.dataset(str(mds.FEAT), format="parquet")
    latest = dataset.to_table(columns=["date"]).to_pandas()["date"].max()
    start = pd.Timestamp(latest) - pd.Timedelta(days=10)
    frame = dataset.to_table(columns=COLS, filter=ds.field("date") >= start).to_pandas()
    frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
    frame = frame[frame["date"] == frame["date"].max()]
    # Forward returns are unknown on the latest day; eligibility must not depend on them.
    frame["entry_ok"] = True
    f = score_block(frame, UNIVERSES["U50"])
    names = pd.read_csv(DATA / "instruments.csv", encoding="utf-8-sig")[["symbol", "name"]]
    PAPER.mkdir(parents=True, exist_ok=True)
    as_of = f["date"].max().date().isoformat()
    lists = {}
    for strat in ("C1", "B0"):
        top = f.nlargest(TOP_N, strat)[["symbol", strat, "amt20", "vol_ratio", "vol20", "dist_high60", "mom20"]]
        top = top.merge(names, on="symbol", how="left").rename(columns={strat: "score"})
        top.insert(0, "strategy", strat)
        top.insert(0, "signal_date", as_of)
        path = PAPER / f"ranking_{strat}_{as_of}.csv"
        top.to_csv(path, index=False, encoding="utf-8-sig")
        lists[strat] = str(path)
    return {"signal_date": as_of, "entry": "next session open", "eligible": int(len(f)), "lists": lists,
            "note": "Paper track only; outcomes are evaluated later from the same data store. No orders."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["cross", "report", "forward"])
    ap.add_argument("--period", choices=list(PERIODS), default="dev")
    args = ap.parse_args()
    if args.stage == "cross":
        print(json.dumps(run_cross()))
    elif args.stage == "report":
        print(json.dumps(run_report(args.period), ensure_ascii=False))
    else:
        print(json.dumps(run_forward(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
