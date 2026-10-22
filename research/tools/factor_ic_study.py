"""Single-factor study for 5/20-day A-share cross-sectional returns (research only).

Each factor is evaluated on its own before any combination:
  * daily Spearman rank IC against next-open -> close(t+h) excess return vs CSI 300,
  * mean IC, ICIR, Newey-West t-statistic (lag h-1: overlapping horizons),
  * share of positive months, top-minus-bottom decile excess return, decile turnover.

Inputs are the QMT daily caches prepared by services/market_alpha_data.py.
Known biases (reported in the output): current-universe membership (survivorship),
no point-in-time ST/industry data, prices adjusted with today's factors (returns
are unaffected), next-open entry ignores limit-up queues, costs not deducted from IC.

    python research/tools/factor_ic_study.py --min-amount 20000000
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from modules.market_clock import SHANGHAI  # noqa: E402
from modules.paths import data_root  # noqa: E402

FACTORS = {
    # name: (description, sign convention: higher value = expected higher return under the hypothesis)
    "reversal_5d": "-(5-day return): short-term reversal",
    "reversal_20d": "-(20-day return): one-month reversal",
    "momentum_60_5": "60-day return skipping the latest 5 days",
    "momentum_20d": "20-day return (the current baseline's momentum term)",
    "low_vol_20d": "-(20-day daily-return volatility)",
    "low_max_ret_20d": "-(max daily return in 20 days): lottery demand",
    "illiquidity_20d": "Amihud: mean |return| / amount over 20 days (small, illiquid names)",
    "low_amount_20d": "-(log 20-day average amount): smaller/less traded names",
    "near_high_250d": "close / 250-day high",
}


def load_panel(root: Path, min_rows: int) -> pd.DataFrame:
    frames = []
    for path in sorted((root / "daily").glob("*.csv")):
        try:
            df = pd.read_csv(path, usecols=["date", "stock_code", "open", "high", "low", "close", "volume", "amount"])
        except (ValueError, OSError):
            continue
        if len(df) >= min_rows:
            frames.append(df)
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    return panel.sort_values(["stock_code", "date"]).reset_index(drop=True)


def add_factors(panel: pd.DataFrame, benchmark: str, horizons: List[int]) -> pd.DataFrame:
    g = panel.groupby("stock_code", sort=False)
    close, amount = panel["close"], panel["amount"]
    ret1 = g["close"].pct_change(fill_method=None)
    panel["reversal_5d"] = -(close / g["close"].shift(5) - 1)
    panel["reversal_20d"] = -(close / g["close"].shift(20) - 1)
    panel["momentum_20d"] = close / g["close"].shift(20) - 1
    panel["momentum_60_5"] = g["close"].shift(5) / g["close"].shift(60) - 1
    panel["_ret1"] = ret1
    panel["low_vol_20d"] = -panel.groupby("stock_code")["_ret1"].transform(lambda s: s.rolling(20, min_periods=20).std())
    panel["low_max_ret_20d"] = -panel.groupby("stock_code")["_ret1"].transform(lambda s: s.rolling(20, min_periods=20).max())
    panel["_illiq"] = ret1.abs() / amount.replace(0, np.nan)
    panel["illiquidity_20d"] = panel.groupby("stock_code")["_illiq"].transform(lambda s: s.rolling(20, min_periods=15).mean()) * 1e8
    panel["amount_20d"] = panel.groupby("stock_code")["amount"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    panel["low_amount_20d"] = -np.log(panel["amount_20d"].replace(0, np.nan))
    panel["near_high_250d"] = close / panel.groupby("stock_code")["high"].transform(lambda s: s.rolling(250, min_periods=120).max())
    panel["history_rows"] = g.cumcount() + 1
    bench = panel.loc[panel["stock_code"] == benchmark, ["date", "open", "close"]].set_index("date")
    next_open = g["open"].shift(-1)
    traded_next = g["volume"].shift(-1).fillna(0) > 0
    for h in horizons:
        exit_close = g["close"].shift(-h)
        fwd = exit_close / next_open - 1
        b_fwd = (bench["close"].shift(-h) / bench["open"].shift(-1) - 1).reindex(panel["date"]).to_numpy()
        panel[f"excess_{h}d"] = (fwd - b_fwd).where(traded_next & (next_open > 0))
    return panel.drop(columns=["_ret1", "_illiq"])


def newey_west_t(series: pd.Series, lag: int) -> float:
    x = series.dropna().to_numpy()
    n = len(x)
    if n < 10:
        return float("nan")
    d = x - x.mean()
    var = d @ d / n
    for k in range(1, min(lag, n - 1) + 1):
        var += 2 * (1 - k / (lag + 1)) * (d[k:] @ d[:-k]) / n
    return float(x.mean() / math.sqrt(var / n)) if var > 0 else float("nan")


def study(panel: pd.DataFrame, horizons: List[int], min_amount: float, min_history: int, min_names: int) -> Dict:
    eligible = panel[(panel["history_rows"] >= min_history) & (panel["amount_20d"] >= min_amount)]
    results: Dict = {}
    for h in horizons:
        target = f"excess_{h}d"
        for name in FACTORS:
            daily_ic, spreads, members = [], [], []
            for day, grp in eligible[["date", "stock_code", name, target]].dropna().groupby("date"):
                if len(grp) < min_names:
                    continue
                daily_ic.append((day, grp[name].rank().corr(grp[target].rank())))
                q = pd.qcut(grp[name].rank(method="first"), 10, labels=False)
                spreads.append((day, grp.loc[q == 9, target].mean() - grp.loc[q == 0, target].mean()))
                members.append(set(grp.loc[q == 9, "stock_code"]))
            if not daily_ic:
                continue
            ic = pd.Series(dict(daily_ic)).sort_index()
            spread = pd.Series(dict(spreads)).sort_index()
            monthly = ic.groupby(ic.index.to_period("M")).mean()
            turnover = [1 - len(a & b) / max(1, len(a)) for a, b in zip(members[:-1], members[1:])]
            results.setdefault(f"{h}d", {})[name] = {
                "days": int(ic.size), "mean_ic": round(float(ic.mean()), 4),
                "icir_daily": round(float(ic.mean() / ic.std()), 3) if ic.std() > 0 else None,
                "newey_west_t": round(newey_west_t(ic, h - 1), 2),
                "positive_month_share": round(float((monthly > 0).mean()), 2),
                "top_minus_bottom_decile_excess_pct": round(100 * float(spread.mean()), 3),
                "top_decile_daily_turnover": round(float(np.mean(turnover)), 3) if turnover else None,
                "first_half_mean_ic": round(float(ic.iloc[: len(ic) // 2].mean()), 4),
                "second_half_mean_ic": round(float(ic.iloc[len(ic) // 2:].mean()), 4),
            }
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=data_root() / "market_alpha")
    parser.add_argument("--benchmark", default="000300.SH")
    parser.add_argument("--horizons", type=int, nargs="+", default=[5, 20])
    parser.add_argument("--min-amount", type=float, default=2e7)
    parser.add_argument("--min-history", type=int, default=120)
    parser.add_argument("--min-names", type=int, default=300)
    parser.add_argument("--out", type=Path, default=ROOT / "research" / "results")
    args = parser.parse_args(argv)
    panel = load_panel(args.data_root, 60)
    stocks = panel["stock_code"].nunique()
    panel = add_factors(panel, args.benchmark, args.horizons)
    results = study(panel, args.horizons, args.min_amount, args.min_history, args.min_names)
    report = {
        "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "stocks_with_cache": int(stocks), "dates": [str(panel["date"].min().date()), str(panel["date"].max().date())],
        "factor_definitions": FACTORS, "results": results,
        "caveats": ["Current-universe membership: delisted names missing (survivorship bias, inflates some factors).",
                    "Front-adjusted prices with today's factors; returns unaffected, levels not point-in-time.",
                    "Next-open entry ignores limit-up/limit-down queues and suspensions after the signal.",
                    "IC is before costs; overlapping horizons handled by Newey-West, cross-sectional "
                    "dependence is not. Several factors were tested: expect some |t|~2 by chance."],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"factor_ic_study_{datetime.now(SHANGHAI):%Y%m%d}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for h, rows in results.items():
        print(f"\n== horizon {h} (stocks with cache: {stocks}) ==")
        print(f"{'factor':18s} {'meanIC':>7s} {'NW t':>6s} {'ICIR':>6s} {'pos.mo':>6s} {'D10-D1%':>8s} {'1st/2nd half IC':>16s}")
        for name, r in sorted(rows.items(), key=lambda kv: -abs(kv[1]["newey_west_t"] or 0)):
            print(f"{name:18s} {r['mean_ic']:7.4f} {r['newey_west_t']:6.2f} {r['icir_daily'] or 0:6.3f} "
                  f"{r['positive_month_share']:6.2f} {r['top_minus_bottom_decile_excess_pct']:8.3f} "
                  f"{r['first_half_mean_ic']:7.4f}/{r['second_half_mean_ic']:.4f}")
    print(f"\nReport: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
