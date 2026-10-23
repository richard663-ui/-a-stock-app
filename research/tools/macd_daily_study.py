"""Daily MACD study, 5/10/20-day horizons (pre-registered: research/preregistration/macd_daily_v1.md).

Stages (memory stays flat; the PC also runs QMT and recorders):
  features   per stock: MACD, events, controls, forward returns -> research_daily/macd_features/*.parquet
  cross      per half-year block: excess returns, event means, rank ICs, Fama-MacBeth -> macd_cross/*.parquet
  report     aggregate a period (dev or holdout) -> research/results/macd_daily_<period>.json

  python research/tools/macd_daily_study.py features
  python research/tools/macd_daily_study.py cross
  python research/tools/macd_daily_study.py report --period dev
  python research/tools/macd_daily_study.py report --period holdout   # once, after the dev verdict exists

Research only: nothing here touches production models or trading.
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
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parents[2]
DATA = Path.home() / "AStockData" / "research_daily"
FEAT = DATA / "macd_features"
CROSS = DATA / "macd_cross"
RESULTS = ROOT / "research" / "results"
VERSION = "macd-daily-study-v1"

HORIZONS = (5, 10, 20)
DEV = ("2015-01-01", "2021-12-31")
HOLDOUT = ("2022-01-01", "2099-12-31")
COST_BP = 30.0
MIN_BARS, MIN_PRICE, MIN_AMOUNT = 120, 2.0, 2e7

EVENTS = ["GC_above", "GC_below", "DC_above", "DC_below", "ZERO_up", "ZERO_down",
          "H_red_exp", "H_red_con", "H_green_exp", "H_green_con", "DIV_bull", "DIV_bear",
          "GC_week_agree", "GC_week_disagree", "GC_above_vwap", "GC_below_vwap",
          "GC_vol_high", "GC_vol_low", "GC_near_high", "GC_near_low",
          "PULLBACK_healthy", "PULLBACK_broken", "ALL"]
LONG_EVENTS = {"GC_above", "GC_below", "ZERO_up", "H_red_exp", "H_green_con", "DIV_bull", "GC_week_agree",
               "GC_week_disagree", "GC_above_vwap", "GC_below_vwap", "GC_vol_high", "GC_vol_low",
               "GC_near_high", "GC_near_low", "PULLBACK_healthy"}
MACD_FEATURES = ["dif_n", "hist_n", "dhist_n", "whist_n"]
CONTROLS = ["rev5", "mom20", "mom60_5", "vol20", "maxret20", "logamt20", "vwap_dev", "vol_ratio", "dist_high60"]


# ----------------------------------------------------------------------------- features

def _macd(close: pd.Series):
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    return dif, dif - dea


def _limit_pct(symbol: str, dates: pd.Series, is_st: pd.Series) -> np.ndarray:
    code = symbol[:6]
    pct = np.full(len(dates), 0.10)
    if code.startswith("688"):
        pct[:] = 0.20
    elif code.startswith(("300", "301")):
        pct[(dates >= pd.Timestamp("2020-08-24")).to_numpy()] = 0.20
    pct[is_st.to_numpy(bool)] = np.minimum(pct[is_st.to_numpy(bool)], 0.05)
    return pct


def stock_features(symbol: str, g: pd.DataFrame, calendar: pd.DatetimeIndex, delisted: bool = False) -> pd.DataFrame:
    g = g.sort_values("date").drop_duplicates("date")
    traded = g[(g["volume_shares"] > 0) & (~g["suspended"].astype(bool)) & g["close"].notna()]
    if len(traded) < MIN_BARS:
        return pd.DataFrame()
    last = traded["date"].iloc[-1]
    if delisted:
        # A delisted stock's last close is its realised exit; keep the calendar running past it.
        after = calendar[calendar > last]
        last = after[min(len(after), max(HORIZONS) + 5) - 1] if len(after) else last
    span = calendar[(calendar >= traded["date"].iloc[0]) & (calendar <= last)]
    t = traded.set_index("date")
    c = t["close"].astype("float64")
    dif, hist = _macd(c)
    ret1 = c.pct_change()
    feats = pd.DataFrame(index=t.index)
    feats["bars"] = np.arange(1, len(t) + 1)
    feats["close_raw"] = t["close_raw"]
    feats["dif_n"] = dif / c
    feats["hist_n"] = hist / c
    feats["dhist_n"] = hist.diff() / c
    hp, dp = hist.shift(1), dif.shift(1)
    gc, dc = (hist > 0) & (hp <= 0), (hist < 0) & (hp >= 0)
    feats["GC_above"], feats["GC_below"] = gc & (dif > 0), gc & (dif <= 0)
    feats["DC_above"], feats["DC_below"] = dc & (dif > 0), dc & (dif <= 0)
    feats["ZERO_up"], feats["ZERO_down"] = (dif > 0) & (dp <= 0), (dif < 0) & (dp >= 0)
    feats["H_red_exp"], feats["H_red_con"] = (hist > 0) & (hist > hp), (hist > 0) & (hist <= hp)
    feats["H_green_exp"], feats["H_green_con"] = (hist < 0) & (hist < hp), (hist < 0) & (hist >= hp)

    # Divergence against the previous 40-day closing extreme 10-40 bars earlier.
    pos = pd.Series(np.arange(len(c)), index=c.index, dtype="float64")
    for name, extreme, better in (("DIV_bull", c.rolling(40).min(), lambda now, then: now > then),
                                  ("DIV_bear", c.rolling(40).max(), lambda now, then: now < then)):
        is_ext = c == extreme
        last = pos.where(is_ext).ffill().shift(10)
        ok = is_ext & last.notna() & ((pos - last) <= 40)
        idx = last.fillna(0).astype(int).to_numpy()
        dif_then = pd.Series(dif.to_numpy()[idx], index=c.index)
        sign_ok = (dif < 0) if name == "DIV_bull" else (dif > 0)
        feats[name] = ok & sign_ok & better(dif, dif_then)

    # Weekly MACD from completed weeks only (week W uses weekly bars up to W-1).
    week = t.index.to_period("W-FRI")
    wclose = c.groupby(week).last()
    _, whist = _macd(wclose)
    feats["whist_n"] = (whist / wclose).shift(1).reindex(week).to_numpy()
    feats["GC_week_agree"] = gc & (feats["whist_n"] > 0)
    feats["GC_week_disagree"] = gc & (feats["whist_n"] <= 0)

    vol = t["volume_shares"].astype("float64")
    amt = t["amount_cny"].astype("float64")
    vwap20 = amt.rolling(20).sum() / vol.rolling(20).sum()
    feats["vwap_dev"] = t["close_raw"] / vwap20 - 1
    feats["vol_ratio"] = vol.rolling(5).mean() / vol.rolling(20).mean()
    feats["dist_high60"] = c / c.rolling(60).max() - 1
    dist_low60 = c / c.rolling(60).min() - 1
    feats["GC_above_vwap"], feats["GC_below_vwap"] = gc & (feats["vwap_dev"] > 0), gc & (feats["vwap_dev"] <= 0)
    feats["GC_vol_high"], feats["GC_vol_low"] = gc & (feats["vol_ratio"] > 1.5), gc & (feats["vol_ratio"] < 1.0)
    feats["GC_near_high"], feats["GC_near_low"] = gc & (feats["dist_high60"] >= -0.05), gc & (dist_low60 <= 0.05)

    feats["rev5"] = c / c.shift(5) - 1
    feats["mom20"] = c / c.shift(20) - 1
    feats["mom60_5"] = c.shift(5) / c.shift(60) - 1
    feats["vol20"] = ret1.rolling(20).std()
    feats["maxret20"] = ret1.rolling(20).max()
    feats["logamt20"] = np.log(amt.rolling(20).mean())
    ma60 = c.rolling(60).mean()
    pull = (c > ma60) & (ma60 > ma60.shift(20)) & (feats["rev5"] <= -0.03)
    feats["PULLBACK_healthy"], feats["PULLBACK_broken"] = pull & (dif > 0), pull & (dif <= 0)
    feats["ret1"] = ret1
    feats["amt20"] = amt.rolling(20).mean()
    is_st = t["is_st"].astype(bool) if "is_st" in t else pd.Series(False, index=t.index)
    feats["is_st"] = is_st

    # Calendar-aligned forward returns: enter at next session's open (if traded and not limit-up),
    # exit at the close h sessions later (last available close if suspended or delisted).
    full = feats.reindex(span)
    close_c = c.reindex(span)
    open_c = t["open"].astype("float64").reindex(span)
    adj = (t["close_raw"] / t["close"]).reindex(span)
    raw_open_next = (open_c * adj).shift(-1)
    pre_next = t["pre_close_raw"].astype("float64").reindex(span).shift(-1)
    limit = _limit_pct(symbol, pd.Series(span), is_st.reindex(span).fillna(False).astype(bool))
    limit_next = np.round(pre_next * (1 + pd.Series(limit, index=span).shift(-1)), 2)
    entry = open_c.shift(-1)
    full["entry_ok"] = entry.notna() & ~(raw_open_next >= 0.995 * limit_next)
    close_ff = close_c.ffill()
    for h in HORIZONS:
        exit_px = close_ff.shift(-h)
        r = exit_px / entry - 1
        # Not enough future data yet (sample end) -> missing, never filled.
        r[span.to_series().shift(-h).isna().to_numpy()] = np.nan
        full[f"R{h}"] = r
    full["traded"] = full["bars"].notna()
    full["symbol"] = symbol
    full.index.name = "date"
    out = full.reset_index()
    for col in out.columns:
        if out[col].dtype == object and col not in ("symbol",):
            out[col] = out[col].astype("boolean").fillna(False).astype(bool)
        elif out[col].dtype == "float64":
            out[col] = out[col].astype("float32")
    return out


def load_calendar() -> pd.DatetimeIndex:
    idx = pd.read_parquet(DATA / "index.parquet", columns=["date", "symbol", "volume_shares"])
    idx = idx[(idx["symbol"] == "000001.SH") & (idx["volume_shares"] > 0)]
    return pd.DatetimeIndex(sorted(idx["date"].unique()))


def run_features() -> dict:
    calendar = load_calendar()
    FEAT.mkdir(parents=True, exist_ok=True)
    sources = sorted((DATA / "bars_qmt").glob("part-*.parquet"))
    if (DATA / "bars_delisted.parquet").exists():
        sources.append(DATA / "bars_delisted.parquet")
    qmt_symbols = set()
    stats = {"stocks": 0, "rows": 0, "delisted_stocks": 0}
    for n, src in enumerate(sources):
        bars = pd.read_parquet(src)
        if src.name == "bars_delisted.parquet":
            bars = bars[~bars["symbol"].isin(qmt_symbols)]   # never double count a code
        else:
            qmt_symbols.update(bars["symbol"].unique())
        delisted = src.name == "bars_delisted.parquet"
        out = [stock_features(sym, g, calendar, delisted) for sym, g in bars.groupby("symbol", sort=False)]
        out = [o for o in out if not o.empty]
        if out:
            frame = pd.concat(out, ignore_index=True)
            frame.to_parquet(FEAT / f"{src.stem}.parquet", index=False)
            stats["rows"] += len(frame)
            stats["stocks"] += len(out)
            if src.name == "bars_delisted.parquet":
                stats["delisted_stocks"] = len(out)
        print(f"features {n + 1}/{len(sources)} {src.name}: {len(out)} stocks", flush=True)
    return stats


# ----------------------------------------------------------------------------- cross sections

def _rank(x: pd.Series) -> pd.Series:
    r = x.rank(pct=True)
    return r - 0.5


def _ols_coef(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    X1 = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    return beta[1:]


def cross_block(frame: pd.DataFrame, sample_dates: set) -> dict:
    """All cross-sectional statistics for one block of dates."""
    elig = (frame["traded"] & (frame["bars"] >= MIN_BARS) & (frame["close_raw"] >= MIN_PRICE)
            & (frame["amt20"] >= MIN_AMOUNT) & ~frame["is_st"] & frame["entry_ok"])
    f = frame[elig].copy()
    f["ALL"] = True
    ev_rows, ic_rows, fm_rows, corr_rows = [], [], [], []
    for date, g in f.groupby("date", sort=True):
        for h in HORIZONS:
            r = g[f"R{h}"]
            ok = r.notna()
            if ok.sum() < 50:
                continue
            gg = g[ok]
            ex = r[ok] - r[ok].mean()
            for e in EVENTS:
                m = gg[e].to_numpy(bool)
                if m.any():
                    ev_rows.append((date, h, e, int(m.sum()), float(ex[m].mean()), float((ex[m] > 0).mean()),
                                    float(r[ok][m].mean())))
            rex = _rank(ex)
            for feat in MACD_FEATURES + CONTROLS:
                x = gg[feat]
                good = x.notna()
                if good.sum() >= 50:
                    ic_rows.append((date, h, feat, float(_rank(x[good]).corr(rex[good]))))
            if date in sample_dates:
                X_all = gg[MACD_FEATURES + CONTROLS]
                good = X_all.notna().all(axis=1).to_numpy()
                if good.sum() >= 200:
                    Xr = X_all[good].apply(_rank).to_numpy()
                    y = rex[good].to_numpy()
                    ctrl = Xr[:, len(MACD_FEATURES):]
                    for j, feat in enumerate(MACD_FEATURES):
                        alone = _ols_coef(y, Xr[:, [j]])[0]
                        with_c = _ols_coef(y, np.column_stack([Xr[:, j], ctrl]))[0]
                        fm_rows.append((date, h, feat, float(alone), float(with_c)))
                    if h == HORIZONS[0]:
                        corr = pd.DataFrame(Xr, columns=MACD_FEATURES + CONTROLS).corr().loc[MACD_FEATURES]
                        for feat in MACD_FEATURES:
                            for ctl in CONTROLS + MACD_FEATURES:
                                corr_rows.append((date, feat, ctl, float(corr.loc[feat, ctl])))
    return {
        "events": pd.DataFrame(ev_rows, columns=["date", "h", "event", "n", "ex_mean", "hit", "raw_mean"]),
        "ic": pd.DataFrame(ic_rows, columns=["date", "h", "feature", "ic"]),
        "fm": pd.DataFrame(fm_rows, columns=["date", "h", "feature", "coef_alone", "coef_controls"]),
        "corr": pd.DataFrame(corr_rows, columns=["date", "feature", "other", "rho"]),
        "market": f.groupby("date").agg(ew_ret1=("ret1", "mean"), n=("ret1", "size")).reset_index(),
    }


def run_cross() -> dict:
    dataset = ds.dataset(str(FEAT), format="parquet")
    calendar = load_calendar()
    sample_dates = set(calendar[::5])
    CROSS.mkdir(parents=True, exist_ok=True)
    cols = (["date", "symbol", "traded", "bars", "close_raw", "amt20", "is_st", "entry_ok", "ret1"]
            + [f"R{h}" for h in HORIZONS] + MACD_FEATURES + CONTROLS + [e for e in EVENTS if e != "ALL"])
    blocks = []
    for year in range(calendar[0].year, calendar[-1].year + 1):
        for lo, hi in ((f"{year}-01-01", f"{year}-06-30"), (f"{year}-07-01", f"{year}-12-31")):
            filt = (ds.field("date") >= pd.Timestamp(lo)) & (ds.field("date") <= pd.Timestamp(hi))
            frame = dataset.to_table(columns=cols, filter=filt).to_pandas()
            if frame.empty:
                continue
            out = cross_block(frame, sample_dates)
            for key, value in out.items():
                value.to_parquet(CROSS / f"{key}_{lo[:7]}.parquet", index=False)
            blocks.append(lo[:7])
            print(f"cross {lo}..{hi}: rows={len(frame)} events={len(out['events'])}", flush=True)
            del frame, out
    return {"blocks": blocks}


# ----------------------------------------------------------------------------- report

def return_kernel(lags: int = 80) -> dict:
    """Exact weights of DIF and HIST (per unit price) on past one-day price changes.

    EMA_a(P)_t = P_t - sum_k (1-a)^(k+1) dP_{t-k}, so DIF and HIST are fixed linear
    filters of past returns: MACD is a re-weighting of momentum/reversal windows.
    """
    k = np.arange(lags)
    a12, a26, a9 = 2 / 13, 2 / 27, 2 / 10
    dif_w = (1 - a26) ** (k + 1) - (1 - a12) ** (k + 1)
    # DEA_t = sum_j a9 (1-a9)^j DIF_{t-j}; DIF_{t-j} puts weight dif_w[k-j] on dP_{t-k}.
    dea_w = np.array([sum(a9 * (1 - a9) ** j * dif_w[i - j] for j in range(i + 1)) for i in k])
    hist_w = dif_w - dea_w
    def summary(w):
        pos = w.clip(min=0)
        return {"weights_first_30": [round(float(x), 4) for x in w[:30]],
                "sum": round(float(w.sum()), 3),
                "positive_mass_centre_days": round(float((k * pos).sum() / pos.sum()), 1),
                "sign_change_lag": int(np.argmax(w < 0)) if (w < 0).any() else None}
    return {"dif": summary(dif_w), "hist": summary(hist_w),
            "reading": "DIF = hump-shaped momentum over roughly the last 1-2 months; HIST = recent returns "
                       "(positive weights) minus older returns (negative weights), i.e. a short-minus-medium "
                       "momentum (acceleration) filter."}


def nw_t(x: np.ndarray, lags: int) -> float:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10:
        return float("nan")
    e = x - x.mean()
    var = e @ e / n
    for k in range(1, min(lags, n - 1) + 1):
        var += 2 * (1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / math.sqrt(var / n)) if var > 0 else float("nan")


def _read(kind: str, lo: str, hi: str) -> pd.DataFrame:
    parts = [pd.read_parquet(p) for p in sorted(CROSS.glob(f"{kind}_*.parquet"))]
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if df.empty:
        return df
    return df[(df["date"] >= pd.Timestamp(lo)) & (df["date"] <= pd.Timestamp(hi))]


def regimes() -> pd.DataFrame:
    m = _read("market", "2000-01-01", "2099-12-31").sort_values("date").set_index("date")
    idx = (1 + m["ew_ret1"].fillna(0)).cumprod()
    vol = m["ew_ret1"].rolling(20).std()
    out = pd.DataFrame(index=m.index)
    out["trend"] = np.where(idx > idx.rolling(120).mean(), "up", "down")
    out["vol"] = np.where(vol > vol.rolling(250, min_periods=120).median(), "high", "low")
    out["year"] = out.index.year
    return out


def event_stats(ev: pd.DataFrame, h: int) -> dict:
    ev = ev.sort_values("date")
    pooled = float(np.average(ev["ex_mean"], weights=ev["n"]))
    return {"events": int(ev["n"].sum()), "dates": int(len(ev)),
            "mean_excess_bp": round(pooled * 1e4, 1),
            "date_mean_excess_bp": round(float(ev["ex_mean"].mean()) * 1e4, 1),
            "nw_t": round(nw_t(ev["ex_mean"].to_numpy(), h), 2),
            "hit_rate": round(float(np.average(ev["hit"], weights=ev["n"])), 4),
            "mean_raw_bp": round(float(np.average(ev["raw_mean"], weights=ev["n"])) * 1e4, 1)}


def run_report(period: str) -> dict:
    lo, hi = DEV if period == "dev" else HOLDOUT
    dev_file = RESULTS / "macd_daily_dev.json"
    if period == "holdout" and not dev_file.exists():
        raise SystemExit("Development verdicts must be written before the holdout is evaluated.")
    reg = regimes()
    ev = _read("events", lo, hi).merge(reg, left_on="date", right_index=True, how="left")
    ic = _read("ic", lo, hi)
    fm = _read("fm", lo, hi)
    corr = _read("corr", lo, hi)
    out = {"version": VERSION, "period": period, "range": [lo, hi], "generated_at":
           datetime.now().astimezone().isoformat(timespec="seconds"), "cost_bp": COST_BP,
           "events": {}, "ic": {}, "fama_macbeth": {}, "redundancy": {}, "kernel": return_kernel(),
           "verdicts": {}}
    years = sorted(ev["year"].dropna().unique())
    for e in EVENTS:
        for h in HORIZONS:
            part = ev[(ev["event"] == e) & (ev["h"] == h)]
            if part.empty:
                continue
            s = event_stats(part, h)
            s["by_year_bp"] = {int(y): round(float(np.average(g["ex_mean"], weights=g["n"])) * 1e4, 1)
                               for y, g in part.groupby("year")}
            for reg_col in ("trend", "vol"):
                s[f"by_{reg_col}"] = {k: event_stats(g, h) for k, g in part.groupby(reg_col) if len(g) >= 20}
            sign = np.sign(s["mean_excess_bp"])
            same = sum(np.sign(v) == sign for v in s["by_year_bp"].values())
            s["years_same_sign"] = f"{same}/{len(s['by_year_bp'])}"
            net = s["mean_excess_bp"] - (COST_BP if e in LONG_EVENTS else 0)
            if period == "dev":
                passed = (abs(s["nw_t"]) >= 3.0 and same >= min(5, len(years))
                          and (e not in LONG_EVENTS or s["mean_excess_bp"] > COST_BP))
            else:
                prior = json.loads(dev_file.read_text(encoding="utf-8"))["events"].get(f"{e}|{h}", {})
                passed = (bool(prior) and np.sign(prior.get("mean_excess_bp", 0)) == sign
                          and abs(s["nw_t"]) >= 2.0 and (e not in LONG_EVENTS or net > 0))
            s["net_of_cost_bp"] = round(net, 1) if e in LONG_EVENTS else None
            s["pass"] = bool(passed)
            out["events"][f"{e}|{h}"] = s
    for (feat, h), g in ic.groupby(["feature", "h"]):
        g = g.sort_values("date")
        out["ic"][f"{feat}|{h}"] = {"mean_ic": round(float(g["ic"].mean()), 4),
                                    "icir": round(float(g["ic"].mean() / g["ic"].std()), 3),
                                    "nw_t": round(nw_t(g["ic"].to_numpy(), h), 2), "dates": int(len(g))}
    for (feat, h), g in fm.groupby(["feature", "h"]):
        g = g.sort_values("date")
        lag = max(1, h // 5)
        alone_t, ctrl_t = nw_t(g["coef_alone"].to_numpy(), lag), nw_t(g["coef_controls"].to_numpy(), lag)
        out["fama_macbeth"][f"{feat}|{h}"] = {
            "coef_alone": round(float(g["coef_alone"].mean()), 5), "t_alone": round(alone_t, 2),
            "coef_with_controls": round(float(g["coef_controls"].mean()), 5), "t_with_controls": round(ctrl_t, 2),
            "dates": int(len(g)), "pass": bool(abs(ctrl_t) >= (3.0 if period == "dev" else 2.0))}
    if not corr.empty:
        mean_corr = corr.groupby(["feature", "other"])["rho"].mean().unstack()
        out["redundancy"] = {f: {k: round(float(v), 3) for k, v in row.items()} for f, row in mean_corr.iterrows()}
    out["verdicts"] = {
        "events_passed": sorted(k for k, v in out["events"].items() if v["pass"]),
        "features_passed_with_controls": sorted(k for k, v in out["fama_macbeth"].items() if v["pass"]),
        "tests_run": len(out["events"]) + len(out["fama_macbeth"]),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"macd_daily_{period}.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return {"written": str(path), **out["verdicts"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["features", "cross", "report"])
    ap.add_argument("--period", choices=["dev", "holdout"], default="dev")
    args = ap.parse_args()
    if args.stage == "features":
        print(json.dumps(run_features()))
    elif args.stage == "cross":
        print(json.dumps(run_cross()))
    else:
        print(json.dumps(run_report(args.period), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
