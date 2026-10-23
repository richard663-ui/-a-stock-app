"""Intraday MACD as an execution-timing tool (pre-registered: research/preregistration/macd_intraday_execution_v1.md).

  python research/tools/macd_intraday_execution.py sample      # 400 stocks, liquidity as of 2025-10-21
  python research/tools/macd_intraday_execution.py download    # QMT 5m bars -> research_daily/bars_5m/
  python research/tools/macd_intraday_execution.py report --period dev|holdout

Costs are versus the day's VWAP in bp, positive = worse than VWAP (buys and sells).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = Path.home() / "AStockData" / "research_daily"
BARS5 = DATA / "bars_5m"
RESULTS = ROOT / "research" / "results"
VERSION = "macd-intraday-execution-v1"
SEED, N_STOCKS, AS_OF = 20261024, 400, "2025-10-21"
START, END = "20251022", "20261023"
PERIODS = {"dev": ("2025-10-23", "2026-04-30"), "holdout": ("2026-05-01", "2026-10-23")}
BARS_PER_DAY = 48
FIRST_SIGNAL_BAR, LAST_SIGNAL_BAR, FALLBACK_BAR, T1000_BAR = 2, 45, 46, 5   # 09:45, 14:50, 14:55, 10:00
BUY_RULES = ["OPEN", "T1000", "M_GC", "M_GC_below", "M_TURN"]
SELL_RULES = ["OPEN", "T1000", "M_DC", "M_DC_above", "M_TURN"]


def nw_t(x, lags=1):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10:
        return float("nan")
    e = x - x.mean()
    var = e @ e / n
    for k in range(1, lags + 1):
        var += 2 * (1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / math.sqrt(var / n)) if var > 0 else float("nan")


def run_sample() -> dict:
    parts = sorted((DATA / "bars_qmt").glob("part-*.parquet"))
    cutoff = pd.Timestamp(AS_OF)
    rows = []
    for p in parts:
        b = pd.read_parquet(p, columns=["date", "symbol", "close_raw", "amount_cny"])
        b = b[(b["date"] <= cutoff) & (b["date"] > cutoff - pd.Timedelta(days=45))]
        for sym, g in b.groupby("symbol"):
            g = g.sort_values("date").tail(20)
            if len(g) == 20 and g["date"].iloc[-1] >= cutoff - pd.Timedelta(days=5):
                rows.append((sym, float(g["amount_cny"].mean()), float(g["close_raw"].iloc[-1])))
    elig = pd.DataFrame(rows, columns=["symbol", "amt20", "close"])
    names = pd.read_csv(DATA / "instruments.csv", encoding="utf-8-sig")
    elig = elig.merge(names[["symbol", "name"]], on="symbol", how="left")
    elig = elig[(elig["amt20"] >= 5e7) & (elig["close"] >= 2) & ~elig["name"].fillna("").str.contains("ST")]
    sample = elig.sample(n=min(N_STOCKS, len(elig)), random_state=SEED).sort_values("symbol")
    DATA.mkdir(parents=True, exist_ok=True)
    sample.to_csv(DATA / "intraday_sample.csv", index=False, encoding="utf-8-sig")
    return {"eligible": int(len(elig)), "sampled": int(len(sample))}


def run_download() -> dict:
    from xtquant import xtdata
    xtdata.enable_hello = False
    symbols = pd.read_csv(DATA / "intraday_sample.csv", encoding="utf-8-sig")["symbol"].tolist()
    BARS5.mkdir(parents=True, exist_ok=True)
    rows, failed = 0, []
    for i in range(0, len(symbols), 20):
        chunk = symbols[i:i + 20]
        xtdata.download_history_data2(chunk, "5m", start_time=START, end_time=END)
        data = xtdata.get_market_data_ex(["time", "open", "high", "low", "close", "volume", "amount"], chunk,
                                         period="5m", start_time=START, end_time=END, dividend_type="none",
                                         fill_data=False) or {}
        frames = []
        for sym in chunk:
            df = data.get(sym)
            if df is None or df.empty:
                failed.append(sym)
                continue
            df = df.reset_index(drop=True)
            df["symbol"] = sym
            frames.append(df)
        if frames:
            out = pd.concat(frames, ignore_index=True)
            out.to_parquet(BARS5 / f"part-{i // 20:03d}.parquet", index=False)
            rows += len(out)
        print(f"{min(i + 20, len(symbols))}/{len(symbols)}", flush=True)
    return {"rows": rows, "failed": failed}


def _day_frames(df: pd.DataFrame):
    """Yield (date, bars, macd arrays) for complete continuous-session days."""
    df = df.sort_values("time").drop_duplicates("time")
    ts = pd.to_datetime(df["time"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")
    df = df.assign(ts=ts.dt.tz_localize(None))
    hhmm = df["ts"].dt.hour * 100 + df["ts"].dt.minute
    df = df[((hhmm >= 935) & (hhmm <= 1130)) | ((hhmm >= 1305) & (hhmm <= 1500))].reset_index(drop=True)
    close = df["close"].astype(float)
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    hist = dif - dif.ewm(span=9, adjust=False).mean()
    df["dif"], df["hist"] = dif, hist
    df["date"] = df["ts"].dt.normalize()
    prev_close = None
    for date, g in df.groupby("date", sort=True):
        if len(g) == BARS_PER_DAY and (g["volume"] > 0).all():
            yield date, g.reset_index(drop=True), prev_close
        prev_close = float(g["close"].iloc[-1])


def _first(mask: np.ndarray):
    idx = np.flatnonzero(mask[FIRST_SIGNAL_BAR:LAST_SIGNAL_BAR + 1])
    return int(idx[0]) + FIRST_SIGNAL_BAR if len(idx) else FALLBACK_BAR


def _limit(symbol: str) -> float:
    return 0.20 if symbol.startswith(("688", "300", "301")) else 0.10


def stock_days(symbol: str, df: pd.DataFrame) -> list:
    out = []
    warm = 0
    for date, g, prev_close in _day_frames(df):
        warm += 1
        if prev_close is None or warm < 3:      # MACD warm-up across the first days
            continue
        o = float(g["open"].iloc[0])
        lim = _limit(symbol)
        if o >= prev_close * (1 + lim) * 0.995 or o <= prev_close * (1 - lim) * 1.005:
            continue
        vwap = float(g["amount"].sum() / (g["volume"].sum() * 100.0))
        close = g["close"].to_numpy(float)
        dif, hist = g["dif"].to_numpy(float), g["hist"].to_numpy(float)
        hp = np.r_[np.nan, hist[:-1]]
        gc, dc = (hist > 0) & (hp <= 0), (hist < 0) & (hp >= 0)
        buy_bar = {"T1000": T1000_BAR, "M_GC": _first(gc), "M_GC_below": _first(gc & (dif < 0)),
                   "M_TURN": _first((hist < 0) & (hist > hp))}
        sell_bar = {"T1000": T1000_BAR, "M_DC": _first(dc), "M_DC_above": _first(dc & (dif > 0)),
                    "M_TURN": _first((hist > 0) & (hist < hp))}
        rec = {"date": date, "symbol": symbol}
        for b in range(BARS_PER_DAY):
            rec[f"bar{b}"] = (close[b] / vwap - 1) * 1e4          # buy cost if filled at bar b's close
        rec["buy_OPEN"], rec["sell_OPEN"] = (o / vwap - 1) * 1e4, (1 - o / vwap) * 1e4
        for rule, b in buy_bar.items():
            rec[f"buy_{rule}"], rec[f"buybar_{rule}"] = (close[b] / vwap - 1) * 1e4, b
        for rule, b in sell_bar.items():
            rec[f"sell_{rule}"], rec[f"sellbar_{rule}"] = (1 - close[b] / vwap) * 1e4, b
        out.append(rec)
    return out


def run_report(period: str) -> dict:
    lo, hi = PERIODS[period]
    if period == "holdout" and not (RESULTS / "macd_intraday_execution_dev.json").exists():
        raise SystemExit("Development result must exist before the holdout is evaluated.")
    recs = []
    for p in sorted(BARS5.glob("part-*.parquet")):
        bars = pd.read_parquet(p)
        for sym, g in bars.groupby("symbol"):
            recs.extend(stock_days(sym, g))
    days = pd.DataFrame(recs)
    days = days[(days["date"] >= pd.Timestamp(lo)) & (days["date"] <= pd.Timestamp(hi))]
    tod_buy = np.array([days[f"bar{b}"].mean() for b in range(BARS_PER_DAY)])
    tod_sell = -tod_buy
    out = {"version": VERSION, "period": period, "range": [lo, hi], "stock_days": int(len(days)),
           "stocks": int(days["symbol"].nunique()), "dates": int(days["date"].nunique()),
           "time_of_day_buy_cost_bp": [round(float(x), 2) for x in tod_buy], "buy": {}, "sell": {},
           "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    for side, rules, tod in (("buy", BUY_RULES, tod_buy), ("sell", SELL_RULES, tod_sell)):
        for rule in rules:
            cost = days[f"{side}_{rule}"]
            if rule == "OPEN":
                ctrl = cost * 0          # open is its own time slot; compare with VWAP only
            else:
                ctrl = days[f"{side}bar_{rule}"].astype(int).map(lambda b: tod[b])
            excess = cost - ctrl
            daily = pd.DataFrame({"date": days["date"], "cost": cost, "excess": excess}).groupby("date").mean()
            fallback = (days[f"{side}bar_{rule}"] == FALLBACK_BAR).mean() if rule.startswith("M_") else 0.0
            vs_vwap, vs_tod = float(cost.mean()), float(excess.mean())
            t_vwap, t_tod = nw_t(daily["cost"]), nw_t(daily["excess"])
            out[side][rule] = {
                "mean_cost_vs_vwap_bp": round(vs_vwap, 2), "t_vs_vwap": round(t_vwap, 2),
                "median_cost_vs_vwap_bp": round(float(cost.median()), 2),
                "share_better_than_vwap": round(float((cost < 0).mean()), 4),
                "mean_vs_time_of_day_bp": round(vs_tod, 2) if rule != "OPEN" else None,
                "t_vs_time_of_day": round(t_tod, 2) if rule != "OPEN" else None,
                "fallback_share": round(float(fallback), 4),
                "useful": bool(rule.startswith("M_") and vs_vwap <= -3 and vs_tod <= -3
                               and abs(t_vwap) >= (3 if period == "dev" else 2)
                               and abs(t_tod) >= (3 if period == "dev" else 2)),
            }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"macd_intraday_execution_{period}.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return {"written": str(path), "stock_days": out["stock_days"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["sample", "download", "report"])
    ap.add_argument("--period", choices=list(PERIODS), default="dev")
    args = ap.parse_args()
    fn = {"sample": run_sample, "download": run_download}.get(args.stage)
    print(json.dumps(fn() if fn else run_report(args.period), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
