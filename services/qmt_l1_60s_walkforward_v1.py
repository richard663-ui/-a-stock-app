# -*- coding: utf-8 -*-
"""Leak-resistant QMT historical L1/tick 60s walk-forward audit.

Research/reference only. This script deliberately does NOT authorize model
promotion or live trading. The current V4R champion and V5R challenger were
designed after observing some of the same historical dates, so this replay is a
sanity/reference backtest rather than pristine prospective OOS evidence.

Hard anti-leak rules:
- QMT epoch-ms timestamps are converted explicitly to Asia/Shanghai.
- historical feature time comes only from the historical tick timestamp;
- HS300/ChiNext context is historical, never get_full_tick/live clock;
- AM/PM sessions are isolated; no lunch/close horizon crossing;
- each 5s sample uses the last tick at-or-before its timestamp (<=5s stale);
- future +60s labels are built only after features and never enter FEATURES;
- day high/low are reconstructed with past-only cumulative lastPrice rather than
  trusting vendor high/low fields that could theoretically be end-of-day values;
- walk-forward test days are chronological and never used for threshold search;
- V4R/V5R model artifacts are written to an isolated audit directory;
- V5R remains challenger-only and no model is copied to the live model folder.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd

VERSION = "qmt-l1-60s-walkforward-v1-20260905"
CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_STOCKS = [
    "301236.SZ", "300308.SZ", "000400.SZ", "600522.SH",
    "601179.SH", "600105.SH", "002916.SZ", "000811.SZ",
]
BENCHMARKS = ["000300.SH", "399006.SZ"]
SESSION_RANGES = [("AM", "09:30:00", "11:30:00"), ("PM", "13:00:00", "15:00:00")]
OUT_ROOT = Path.home() / "AStockData" / "qmt_l1_walkforward"
RECORDER_VERSION = "qmt-history-replay-l1-v1-20260905"
STALE_SECONDS = 5.0
HURDLE_BP = 2.0
MIN_HISTORY_DAYS = 6


def _f(v: Any, d: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except Exception:
        return d


def _arr(v: Any) -> List[float]:
    if isinstance(v, np.ndarray):
        vals = v.tolist()
    elif isinstance(v, (list, tuple)):
        vals = list(v)
    else:
        return []
    out: List[float] = []
    for x in vals:
        try:
            z = float(x)
            if math.isfinite(z):
                out.append(z)
        except Exception:
            pass
    return out


def _to_frame(obj: Any) -> pd.DataFrame:
    if isinstance(obj, pd.DataFrame):
        return obj.copy()
    if isinstance(obj, np.ndarray):
        try:
            return pd.DataFrame.from_records(obj)
        except Exception:
            return pd.DataFrame(obj)
    if isinstance(obj, list):
        return pd.DataFrame(obj)
    return pd.DataFrame()


def _cn_timestamps(frame: pd.DataFrame) -> pd.Series:
    """QMT tick time is epoch-ms; convert UTC epoch to China local wall time."""
    if "time" in frame.columns:
        raw = pd.to_numeric(frame["time"], errors="coerce")
        ts = pd.to_datetime(raw, unit="ms", errors="coerce", utc=True)
        if ts.notna().any():
            return ts.dt.tz_convert(CN_TZ).dt.tz_localize(None)
    if "stime" in frame.columns:
        ts = pd.to_datetime(frame["stime"], errors="coerce")
        if ts.notna().any():
            try:
                return ts.dt.tz_localize(CN_TZ).dt.tz_localize(None)
            except Exception:
                return ts
    ts = pd.to_datetime(frame.index, errors="coerce")
    return pd.Series(ts, index=frame.index)


def _book_fields(row: pd.Series) -> Tuple[float, float, float, float, float, float, float]:
    bp, ap = _arr(row.get("bidPrice")), _arr(row.get("askPrice"))
    bv, av = _arr(row.get("bidVol")), _arr(row.get("askVol"))
    bid1, ask1 = (bp[0] if bp else 0.0), (ap[0] if ap else 0.0)
    bid5, ask5 = sum(bv[:5]), sum(av[:5])
    depth_imb = (bid5 - ask5) / (bid5 + ask5) * 100.0 if bid5 + ask5 > 0 else 0.0
    weights = [1.0, 0.78, 0.58, 0.40, 0.25]
    wb = sum((bv[i] if i < len(bv) else 0.0) * weights[i] for i in range(5))
    wa = sum((av[i] if i < len(av) else 0.0) * weights[i] for i in range(5))
    weighted_pressure = wb / (wb + wa) * 100.0 if wb + wa > 0 else 50.0
    bv1, av1 = (bv[0] if bv else 0.0), (av[0] if av else 0.0)
    micro = (ask1 * bv1 + bid1 * av1) / (bv1 + av1) if bid1 > 0 and ask1 > 0 and bv1 + av1 > 0 else 0.0
    return bid1, ask1, bid5, ask5, depth_imb, weighted_pressure, micro


def _normalize_raw(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy().reset_index(drop=True)
    out["_ts"] = _cn_timestamps(out)
    for c in ("lastPrice", "volume", "amount", "lastClose"):
        out[c] = pd.to_numeric(out.get(c), errors="coerce")
    out = out.dropna(subset=["_ts", "lastPrice"]).copy()
    out = out[out["lastPrice"] > 0].sort_values("_ts")
    out = out.drop_duplicates(subset=["_ts", "lastPrice", "volume"], keep="last").reset_index(drop=True)
    if out.empty:
        return out

    books = out.apply(_book_fields, axis=1, result_type="expand")
    books.columns = ["bid1", "ask1", "depth5_bid", "depth5_ask", "depth5_imbalance_pct", "_weighted_pressure", "microprice"]
    out = pd.concat([out, books], axis=1)
    out["valid_book"] = (out["bid1"] > 0) & (out["ask1"] >= out["bid1"])
    mid = (out["bid1"] + out["ask1"]) / 2.0
    out["mid_price"] = np.where(out["valid_book"], mid, out["lastPrice"])
    out["spread_pct"] = np.where(
        out["valid_book"] & (out["mid_price"] > 0),
        (out["ask1"] - out["bid1"]) / out["mid_price"] * 100.0, np.nan,
    )
    out["microprice"] = np.where(out["microprice"] > 0, out["microprice"], out["mid_price"])
    out["microprice_vs_mid_pct"] = np.where(
        out["mid_price"] > 0, (out["microprice"] / out["mid_price"] - 1.0) * 100.0, 0.0,
    )

    # Past-only day extremes. Do not trust vendor high/low for historical feature creation.
    out["trade_date"] = out["_ts"].dt.date.astype(str)
    out["cum_high"] = out.groupby("trade_date")["lastPrice"].cummax()
    out["cum_low"] = out.groupby("trade_date")["lastPrice"].cummin()

    prev_price = out["lastPrice"].shift(1)
    prev_bid = out["bid1"].shift(1)
    prev_ask = out["ask1"].shift(1)
    dvol = out["volume"].diff().fillna(0.0).clip(lower=0.0)
    sign = np.zeros(len(out), dtype=int)
    last_sign = 0
    for i in range(1, len(out)):
        p, pp, b, a = _f(out.at[i, "lastPrice"]), _f(prev_price.iat[i]), _f(prev_bid.iat[i]), _f(prev_ask.iat[i])
        if a > 0 and p >= a:
            last_sign = 1
        elif b > 0 and p <= b:
            last_sign = -1
        elif p > pp:
            last_sign = 1
        elif p < pp:
            last_sign = -1
        sign[i] = last_sign
    out["_buy_vol"] = np.where(sign > 0, dvol, 0.0)
    out["_sell_vol"] = np.where(sign < 0, dvol, 0.0)

    idx = out.set_index("_ts", drop=False)
    buy60 = idx["_buy_vol"].rolling("60s", min_periods=1).sum()
    sell60 = idx["_sell_vol"].rolling("60s", min_periods=1).sum()
    denom = buy60 + sell60
    idx["tick_buy_pct"] = np.where(denom > 0, buy60 / denom * 100.0, 50.0)
    p12 = idx["_weighted_pressure"].rolling("12s", min_periods=1).mean()
    p35 = idx["_weighted_pressure"].rolling("35s", min_periods=1).mean()
    idx["book_buy_pressure_pct"] = p12
    idx["pressure_change_pct"] = p12 - p35
    return idx.reset_index(drop=True)


def _download(xtdata: Any, symbol: str, start: str, end: str) -> pd.DataFrame:
    xtdata.download_history_data(symbol, "tick", start, end)
    data = xtdata.get_market_data_ex(
        field_list=[], stock_list=[symbol], period="tick", start_time=start,
        end_time=end, count=-1, dividend_type="none", fill_data=False,
    ) or {}
    return _normalize_raw(_to_frame(data.get(symbol)), symbol)


def _session_grid(day_raw: pd.DataFrame, day_text: str, session: str, start_hms: str, end_hms: str) -> pd.DataFrame:
    start = pd.Timestamp(f"{day_text} {start_hms}")
    end = pd.Timestamp(f"{day_text} {end_hms}")
    raw = day_raw[(day_raw["_ts"] >= start) & (day_raw["_ts"] <= end)].copy()
    if raw.empty:
        return pd.DataFrame()
    grid = pd.DataFrame({"_ts": pd.date_range(start, end, freq="5s")})
    selected = [
        "_ts", "lastPrice", "lastClose", "volume", "amount", "bid1", "ask1",
        "mid_price", "spread_pct", "depth5_imbalance_pct", "microprice_vs_mid_pct",
        "tick_buy_pct", "book_buy_pressure_pct", "pressure_change_pct", "cum_high", "cum_low", "valid_book",
    ]
    right = raw[selected].rename(columns={"_ts": "_event_ts"}).sort_values("_event_ts")
    g = pd.merge_asof(grid.sort_values("_ts"), right, left_on="_ts", right_on="_event_ts", direction="backward")
    g["age_s"] = (g["_ts"] - g["_event_ts"]).dt.total_seconds()
    g["fresh"] = g["age_s"].between(0.0, STALE_SECONDS, inclusive="both")
    g["session"] = session
    g["trade_date"] = day_text

    for sec in (10, 30, 60, 120):
        periods = sec // 5
        prev = g["lastPrice"].shift(periods)
        g[f"change_{sec}s_pct"] = np.where(prev > 0, (g["lastPrice"] / prev - 1.0) * 100.0, np.nan)

    p60_vol = g["volume"].shift(12)
    p60_amt = g["amount"].shift(12)
    dv = g["volume"] - p60_vol
    da = g["amount"] - p60_amt
    vwap = np.where((dv > 0) & (da > 0), da / (dv * 100.0), np.nan)
    g["above_vwap_pct"] = np.where(vwap > 0, (g["lastPrice"] / vwap - 1.0) * 100.0, 0.0)
    g["day_return_pct"] = np.where(g["lastClose"] > 0, (g["lastPrice"] / g["lastClose"] - 1.0) * 100.0, 0.0)
    g["distance_high_pct"] = np.where(g["cum_high"] > 0, (g["lastPrice"] / g["cum_high"] - 1.0) * 100.0, 0.0)
    g["distance_low_pct"] = np.where(g["cum_low"] > 0, (g["lastPrice"] / g["cum_low"] - 1.0) * 100.0, 0.0)

    # Future labels are created after all current/past features above.
    g["mid_5"] = g["mid_price"].shift(-1)
    g["mid_15"] = g["mid_price"].shift(-3)
    g["mid_30"] = g["mid_price"].shift(-6)
    g["mid_60"] = g["mid_price"].shift(-12)
    g["last_60"] = g["lastPrice"].shift(-12)
    future_mids = pd.concat([g["mid_price"].shift(-11), g["mid_price"].shift(-12), g["mid_price"].shift(-13)], axis=1)
    g["smoothed_mid_60"] = future_mids.mean(axis=1, skipna=False)
    g["future_bid_60"] = g["bid1"].shift(-12)
    for h in (5, 15, 30, 60):
        g[f"ret_mid_{h}_pct"] = np.where(g["mid_price"] > 0, (g[f"mid_{h}"] / g["mid_price"] - 1.0) * 100.0, np.nan)
    g["ret_last_60_pct"] = np.where(g["lastPrice"] > 0, (g["last_60"] / g["lastPrice"] - 1.0) * 100.0, np.nan)
    g["ret_smoothed_mid_60_pct"] = np.where(g["mid_price"] > 0, (g["smoothed_mid_60"] / g["mid_price"] - 1.0) * 100.0, np.nan)
    g["ret_ask_to_bid_60_pct"] = np.where(
        (g["ask1"] > 0) & (g["future_bid_60"] > 0), (g["future_bid_60"] / g["ask1"] - 1.0) * 100.0, np.nan,
    )
    g["label_threshold_pct"] = np.maximum(0.01, pd.to_numeric(g["spread_pct"], errors="coerce").fillna(0.0) * 0.75)

    minute = g["_ts"].dt.hour * 60 + g["_ts"].dt.minute + g["_ts"].dt.second / 60.0
    g["minute_of_day"] = minute
    # Require current quote freshness and t+55/t+60/t+65 to remain in this same session.
    future_fresh = g["fresh"].shift(-11).fillna(False) & g["fresh"].shift(-12).fillna(False) & g["fresh"].shift(-13).fillna(False)
    feature_ready = g["change_60s_pct"].notna()  # 60s minimum history; 120s may be imputed early in session.
    g["valid"] = (
        g["fresh"].fillna(False) & g["valid_book"].fillna(False) & future_fresh & feature_ready
        & g["smoothed_mid_60"].notna() & g["mid_60"].notna()
    )
    return g


def _benchmark_map(raw: pd.DataFrame, trade_dates: Iterable[str]) -> pd.DataFrame:
    pieces: List[pd.DataFrame] = []
    for d in trade_dates:
        day = raw[raw["trade_date"] == d].copy()
        for session, s, e in SESSION_RANGES:
            g = _session_grid(day, d, session, s, e)
            if g.empty:
                continue
            pieces.append(g[["_ts", "day_return_pct", "fresh"]].copy())
    if not pieces:
        return pd.DataFrame(columns=["_ts", "day_return_pct", "fresh"])
    return pd.concat(pieces, ignore_index=True).drop_duplicates("_ts", keep="last").sort_values("_ts")


def _features(row: pd.Series, hs_ret: float, cyb_ret: float) -> Dict[str, Any]:
    minute = _f(row.get("minute_of_day"))
    return {
        "minute_of_day": minute,
        "session_am": int(570 <= minute < 690),
        "phase_open_core": int(570 <= minute < 630),
        "phase_am_late": int(630 <= minute < 690),
        "phase_pm": int(780 <= minute < 900),
        "spread_pct": _f(row.get("spread_pct")),
        "depth5_imbalance_pct": _f(row.get("depth5_imbalance_pct")),
        "microprice_vs_mid_pct": _f(row.get("microprice_vs_mid_pct")),
        "day_return_pct": _f(row.get("day_return_pct")),
        "distance_high_pct": _f(row.get("distance_high_pct")),
        "distance_low_pct": _f(row.get("distance_low_pct")),
        "volume": _f(row.get("volume")), "amount": _f(row.get("amount")),
        "change_10s_pct": _f(row.get("change_10s_pct")), "change_30s_pct": _f(row.get("change_30s_pct")),
        "change_60s_pct": _f(row.get("change_60s_pct")), "change_120s_pct": _f(row.get("change_120s_pct"), np.nan),
        "above_vwap_pct": _f(row.get("above_vwap_pct")),
        "tick_buy_pct": _f(row.get("tick_buy_pct"), 50.0),
        "book_buy_pressure_pct": _f(row.get("book_buy_pressure_pct"), 50.0),
        "pressure_change_pct": _f(row.get("pressure_change_pct")),
        "market_hs300_return_pct": hs_ret, "market_chinext_return_pct": cyb_ret,
        "relative_to_hs300_pct": _f(row.get("day_return_pct")) - hs_ret,
        "relative_to_chinext_pct": _f(row.get("day_return_pct")) - cyb_ret,
    }


def _label(ret: float, threshold: float) -> Optional[int]:
    if not math.isfinite(ret):
        return None
    return 1 if ret > threshold else -1 if ret < -threshold else 0


def _schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS training_samples_v2 (
      id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, sample_bucket INTEGER NOT NULL,
      generated_ts REAL NOT NULL, generated_at TEXT NOT NULL, session TEXT NOT NULL,
      last_price REAL NOT NULL, bid1 REAL, ask1 REAL, mid_price REAL NOT NULL, spread_pct REAL,
      label_threshold_pct REAL, true_l2 INTEGER NOT NULL DEFAULT 0, core_l2_ready INTEGER NOT NULL DEFAULT 0,
      l2_available_count INTEGER NOT NULL DEFAULT 0, baseline_direction TEXT, baseline_agreement INTEGER,
      baseline_high_confidence INTEGER NOT NULL DEFAULT 0, features_json TEXT NOT NULL,
      mid_5 REAL, mid_15 REAL, mid_30 REAL, mid_60 REAL,
      ret_mid_5_pct REAL, ret_mid_15_pct REAL, ret_mid_30_pct REAL, ret_mid_60_pct REAL,
      last_60 REAL, ret_last_60_pct REAL, smoothed_mid_60 REAL, ret_smoothed_mid_60_pct REAL,
      label_last_60 INTEGER, label_mid_60 INTEGER, label_smoothed_mid_60 INTEGER,
      labeled_at TEXT, valid INTEGER NOT NULL DEFAULT 0, invalid_reason TEXT, recorder_version TEXT NOT NULL,
      future_bid_60 REAL, ret_ask_to_bid_60_pct REAL,
      UNIQUE(symbol,sample_bucket,recorder_version)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_symbol_time ON training_samples_v2(symbol,generated_ts)")


def _write_day(root: Path, day: str, rows: List[Tuple[Any, ...]]) -> int:
    folder = root / "training" / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "l2_training.sqlite3"
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        _schema(conn)
        conn.executemany("""
        INSERT INTO training_samples_v2 (
          symbol,sample_bucket,generated_ts,generated_at,session,last_price,bid1,ask1,mid_price,spread_pct,
          label_threshold_pct,true_l2,core_l2_ready,l2_available_count,baseline_direction,baseline_agreement,
          baseline_high_confidence,features_json,mid_5,mid_15,mid_30,mid_60,ret_mid_5_pct,ret_mid_15_pct,
          ret_mid_30_pct,ret_mid_60_pct,last_60,ret_last_60_pct,smoothed_mid_60,ret_smoothed_mid_60_pct,
          label_last_60,label_mid_60,label_smoothed_mid_60,labeled_at,valid,invalid_reason,recorder_version,
          future_bid_60,ret_ask_to_bid_60_pct
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _build_dataset(xtdata: Any, stocks: List[str], start: str, end: str, out_root: Path) -> Dict[str, Any]:
    raw: Dict[str, pd.DataFrame] = {}
    for s in stocks + BENCHMARKS:
        print(f"[DOWNLOAD] {s}")
        raw[s] = _download(xtdata, s, start, end)
        print(f"  rows={len(raw[s])}")
    dates = sorted(set.intersection(*[
        set(x["trade_date"].dropna().astype(str).unique()) for x in raw.values() if not x.empty
    ])) if all(not x.empty for x in raw.values()) else []
    if len(dates) < MIN_HISTORY_DAYS:
        raise RuntimeError(f"common history too short: {len(dates)} days")

    hs = _benchmark_map(raw["000300.SH"], dates).rename(columns={"day_return_pct": "hs", "fresh": "hs_fresh"})
    cy = _benchmark_map(raw["399006.SZ"], dates).rename(columns={"day_return_pct": "cy", "fresh": "cy_fresh"})
    market = hs.merge(cy, on="_ts", how="outer").sort_values("_ts")

    by_day: Dict[str, List[Tuple[Any, ...]]] = {d: [] for d in dates}
    per_symbol: Dict[str, Any] = {}
    for symbol in stocks:
        valid_count = 0
        exec_count = 0
        frame = raw[symbol]
        for d in dates:
            day = frame[frame["trade_date"] == d].copy()
            for session, s0, e0 in SESSION_RANGES:
                g = _session_grid(day, d, session, s0, e0)
                if g.empty:
                    continue
                g = g.merge(market[["_ts", "hs", "cy", "hs_fresh", "cy_fresh"]], on="_ts", how="left")
                g["valid"] = g["valid"] & g["hs_fresh"].fillna(False) & g["cy_fresh"].fillna(False)
                for _, r in g[g["valid"]].iterrows():
                    ts = pd.Timestamp(r["_ts"])
                    aware = ts.tz_localize(CN_TZ)
                    epoch = aware.timestamp()
                    threshold = _f(r.get("label_threshold_pct"), 0.01)
                    ret_sm = _f(r.get("ret_smoothed_mid_60_pct"), np.nan)
                    features = _features(r, _f(r.get("hs")), _f(r.get("cy")))
                    # Defensive leakage scan at row creation.
                    if any(k.startswith(("ret_", "mid_60", "smoothed_mid", "future_", "label_")) for k in features):
                        raise RuntimeError("future-derived field entered historical FEATURES")
                    vals = (
                        symbol, int(epoch // 5) * 5, epoch, aware.isoformat(timespec="milliseconds"), session,
                        _f(r.get("lastPrice")), _f(r.get("bid1")), _f(r.get("ask1")), _f(r.get("mid_price")),
                        _f(r.get("spread_pct")), threshold, 0, 0, 0, "WATCH", 0, 0,
                        json.dumps(features, ensure_ascii=False, separators=(",", ":")),
                        _f(r.get("mid_5"), np.nan), _f(r.get("mid_15"), np.nan), _f(r.get("mid_30"), np.nan), _f(r.get("mid_60"), np.nan),
                        _f(r.get("ret_mid_5_pct"), np.nan), _f(r.get("ret_mid_15_pct"), np.nan), _f(r.get("ret_mid_30_pct"), np.nan), _f(r.get("ret_mid_60_pct"), np.nan),
                        _f(r.get("last_60"), np.nan), _f(r.get("ret_last_60_pct"), np.nan), _f(r.get("smoothed_mid_60"), np.nan), ret_sm,
                        _label(_f(r.get("ret_last_60_pct"), np.nan), threshold), _label(_f(r.get("ret_mid_60_pct"), np.nan), threshold), _label(ret_sm, threshold),
                        (aware + pd.Timedelta(seconds=65)).isoformat(timespec="seconds"), 1, None, RECORDER_VERSION,
                        _f(r.get("future_bid_60"), np.nan), _f(r.get("ret_ask_to_bid_60_pct"), np.nan),
                    )
                    by_day[d].append(vals)
                    valid_count += 1
                    exec_count += int(math.isfinite(_f(r.get("ret_ask_to_bid_60_pct"), np.nan)))
        per_symbol[symbol] = {"valid_rows": valid_count, "exact_ask_to_bid_rows": exec_count}

    total = 0
    for d, rows in by_day.items():
        total += _write_day(out_root, d, rows)
        print(f"[DATASET] {d} rows={len(rows)}")
    return {"trade_dates": dates, "rows": total, "per_symbol": per_symbol}


def _copy_fold(src: Path, dst: Path, dates: List[str]) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    for d in dates:
        s = src / "training" / d / "l2_training.sqlite3"
        t = dst / "training" / d / "l2_training.sqlite3"
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, t)


def _session_safe_expand_patch() -> Tuple[Any, Any]:
    import services.train_l1_60s_model_v3 as v3
    original = v3._expand

    def patched(raw: pd.DataFrame) -> pd.DataFrame:
        out = original(raw)
        if out.empty or "session" not in out.columns:
            return out
        keys = ["symbol", "trade_date", "session"]
        for src, dst in (
            ("volume", "volume_delta_5s"), ("amount", "amount_delta_5s"),
            ("spread_pct", "spread_change_5s"), ("depth5_imbalance_pct", "depth5_imbalance_change_5s"),
            ("microprice_vs_mid_pct", "microprice_change_5s"), ("tick_buy_pct", "tick_buy_change_5s"),
            ("book_buy_pressure_pct", "book_pressure_change_5s"),
        ):
            if src in out.columns:
                out[dst] = out.groupby(keys, sort=False, dropna=False)[src].diff().fillna(0.0)
        out["volume_delta_5s"] = pd.to_numeric(out.get("volume_delta_5s"), errors="coerce").fillna(0.0).clip(lower=0.0)
        out["amount_delta_5s"] = pd.to_numeric(out.get("amount_delta_5s"), errors="coerce").fillna(0.0).clip(lower=0.0)
        out["log_volume_delta_5s"] = np.log1p(out["volume_delta_5s"])
        out["log_amount_delta_5s"] = np.log1p(out["amount_delta_5s"])
        out["volume_accel_5s"] = out.groupby(keys, sort=False, dropna=False)["volume_delta_5s"].diff().fillna(0.0)
        out["amount_accel_5s"] = out.groupby(keys, sort=False, dropna=False)["amount_delta_5s"].diff().fillna(0.0)
        return out

    v3._expand = patched
    return v3, original


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _exact_execution(item: Dict[str, Any], frame: pd.DataFrame, variant: str) -> Dict[str, Any]:
    import services.train_l1_60s_model_v4 as core
    import services.train_l1_60s_model_v5_challenger as v5
    import services.train_l2_60s_model_v3 as splitbase
    model_path = Path(str(item.get("model_path") or ""))
    if not model_path.exists() or frame.empty:
        return {"n": 0}
    bundle = joblib.load(model_path)
    if variant == "V5R":
        _, _, test, _ = v5._robust_split(frame)
    else:
        _, _, test, _ = splitbase._split(frame)
    test = splitbase._nonoverlap(test)
    if test.empty or "ret_ask_to_bid_60_pct" not in test.columns:
        return {"n": 0}
    up_p = core._positive_probability(bundle["up_model"], test[core.FEATURES])
    dn_p = core._positive_probability(bundle["down_model"], test[core.FEATURES])
    pred = core._combine(up_p, dn_p, float(bundle["up_threshold"]), float(bundle["down_threshold"]))
    mask = (pred == 1) & pd.to_numeric(test["ret_ask_to_bid_60_pct"], errors="coerce").notna().to_numpy()
    vals = pd.to_numeric(test.loc[mask, "ret_ask_to_bid_60_pct"], errors="coerce").dropna().to_numpy(float)
    return {
        "n": int(len(vals)),
        "avg_ask_to_bid_60_edge_bp": float(vals.mean() * 100.0) if len(vals) else None,
        "median_ask_to_bid_60_edge_bp": float(np.median(vals) * 100.0) if len(vals) else None,
        "positive_ask_to_bid_60_pct": float((vals > 0).mean() * 100.0) if len(vals) else None,
        "note": "signal-quality execution check only; A-share cash T+1 prevents literal 60s same-day roundtrip",
    }


def _run_fold(dataset_root: Path, audit_root: Path, dates: List[str], fold_no: int) -> Dict[str, Any]:
    import services.train_l1_60s_model_v3 as v3
    import services.train_l1_60s_model_v4 as core
    import services.train_l1_60s_model_v4r as v4r
    import services.train_l1_60s_model_v5_challenger as v5
    import services.train_l1_60s_model_v5r as v5r

    test_day = dates[-1]
    fold_root = audit_root / f"fold_{fold_no:02d}_{test_day}"
    data_root = fold_root / "data"
    models_v4 = fold_root / "models_v4"
    models_v5 = fold_root / "models_v5"
    _copy_fold(dataset_root, data_root, dates)

    # Isolate all artifacts. No live/champion model folder is touched.
    core.MODEL_DIR = models_v4
    v4r.MODEL_DIR = models_v4
    v5.MODEL_DIR = models_v5
    v5r.MODEL_DIR = models_v5

    print(f"[FOLD {fold_no}] train/val history through {dates[-2]} -> TEST {test_day}")
    rc4 = int(v4r.train("ALL", 600, data_root, HURDLE_BP))
    rc5 = int(v5r.train("ALL", 600, data_root, HURDLE_BP))
    r4 = _load_json(models_v4 / "ALL_training_report_latest.json")
    r5 = _load_json(models_v5 / "ALL_training_report_latest.json")

    # Prepared frame keeps exact historical ask->future bid column from SQLite.
    prepared = core._prepare("ALL", data_root, HURDLE_BP)
    for family, item in r4.get("models", {}).items():
        item["exact_up_entry_execution"] = _exact_execution(item, prepared, "V4R")
    for family, item in r5.get("models", {}).items():
        item["exact_up_entry_execution"] = _exact_execution(item, prepared, "V5R")

    return {
        "fold": fold_no, "history_dates": dates, "test_day": test_day,
        "v4r_rc": rc4, "v5r_rc": rc5,
        "v4r": r4, "v5r": r5,
    }


def _aggregate(folds: List[Dict[str, Any]], variant: str, family: str) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for f in folds:
        rep = f.get(variant.lower()) or {}
        item = (rep.get("models") or {}).get(family) or {}
        m = item.get("selected_threshold_test_nonoverlap") or {}
        if m:
            rows.append({"day": f["test_day"], **m, "exec": item.get("exact_up_entry_execution") or {}})
    n = sum(int(x.get("directional_predictions") or 0) for x in rows)
    total_test = sum(int(x.get("n") or 0) for x in rows)
    correct = sum((float(x.get("directional_accuracy_pct") or 0.0) / 100.0) * int(x.get("directional_predictions") or 0) for x in rows)
    gross_num = sum((float(x.get("avg_gross_edge_bp") or 0.0)) * int(x.get("directional_predictions") or 0) for x in rows)
    net_num = sum((float(x.get("avg_net_edge_bp") or 0.0)) * int(x.get("directional_predictions") or 0) for x in rows)
    pos_days = sum(float(x.get("avg_net_edge_bp") or -1e9) > 0 for x in rows)
    exec_n = sum(int((x.get("exec") or {}).get("n") or 0) for x in rows)
    exec_num = sum(float((x.get("exec") or {}).get("avg_ask_to_bid_60_edge_bp") or 0.0) * int((x.get("exec") or {}).get("n") or 0) for x in rows)
    acc = 100.0 * correct / n if n else None
    # Wilson lower bound for directional accuracy; useful against tiny-sample headline accuracy.
    wilson = None
    if n:
        p = correct / n; z = 1.96
        den = 1 + z*z/n
        center = (p + z*z/(2*n)) / den
        half = z * math.sqrt((p*(1-p) + z*z/(4*n))/n) / den
        wilson = 100.0 * max(0.0, center - half)
    coverage = 100.0 * n / total_test if total_test else None
    net = net_num / n if n else None
    suspicious = bool(n >= 100 and (coverage or 0.0) >= 5.0 and (acc or 0.0) >= 85.0)
    return {
        "folds": len(rows), "directional_predictions": n, "test_rows": total_test,
        "directional_accuracy_pct": acc, "accuracy_wilson_95_lower_pct": wilson,
        "directional_coverage_pct": coverage,
        "avg_gross_edge_bp": gross_num / n if n else None,
        "avg_net_edge_bp": net,
        "positive_net_edge_days": pos_days,
        "exact_up_entry_execution_n": exec_n,
        "avg_exact_ask_to_bid_60_edge_bp": exec_num / exec_n if exec_n else None,
        "suspiciously_high_accuracy_leakage_flag": suspicious,
        "daily": rows,
    }


def _null_control(dataset_root: Path, dates: List[str]) -> Dict[str, Any]:
    """One frozen final-fold label-shuffle control. Good pipeline should collapse toward noise."""
    try:
        from sklearn.base import clone
        import services.train_l1_60s_model_v4 as core
        import services.train_l1_60s_model_v5_challenger as v5
        import services.train_l2_60s_model_v3 as splitbase
        frame = core._prepare("ALL", dataset_root, HURDLE_BP)
        # Restrict to history through final date; robust split uses final day as test.
        frame = frame[frame["trade_date"].isin(dates)].copy()
        tr, va, te, _ = v5._robust_split(frame)
        tr = core._thin(tr, core.TRAIN_THIN_SECONDS)
        va, te = splitbase._nonoverlap(va), splitbase._nonoverlap(te)
        if tr.empty or te.empty:
            return {"ok": False, "reason": "empty_null_split"}
        rng = np.random.default_rng(20260905)
        results = []
        for seed_i in range(3):
            up = v5._models()["logistic_balanced"]
            dn = v5._models()["logistic_balanced"]
            yup = tr[core.UP_TARGET].astype(int).to_numpy().copy(); rng.shuffle(yup)
            ydn = tr[core.DOWN_TARGET].astype(int).to_numpy().copy(); rng.shuffle(ydn)
            up.fit(tr[core.FEATURES], yup); dn.fit(tr[core.FEATURES], ydn)
            ut, _, _ = v5._robust_choose_head(up, va, 1, HURDLE_BP, core.UP_TARGET)
            dt, _, _ = v5._robust_choose_head(dn, va, -1, HURDLE_BP, core.DOWN_TARGET)
            m = core._evaluate_combined(up, dn, te, ut, dt, HURDLE_BP)
            results.append(m)
        accs = [x.get("directional_accuracy_pct") for x in results if x.get("directional_accuracy_pct") is not None]
        return {
            "ok": True, "runs": results,
            "median_directional_accuracy_pct": float(np.median(accs)) if accs else None,
            "note": "labels shuffled only in training; validation/test remain chronological real labels",
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _sync_cloud(report: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from modules.cloud_bridge import CloudBridge, load_bridge_config
        cfg = load_bridge_config(); bridge = CloudBridge(cfg, timeout=20.0)
        payload = {
            "bridge_id": cfg.bridge_id, "scope": "QMT_L1_60S_WALKFORWARD",
            "trainer_version": VERSION, "generated_at": report["generated_at"],
            "maturity": "HISTORICAL_REFERENCE_ONLY",
            "protocol": "QMT_TICK_5S_REPLAY__CHRONOLOGICAL_WALK_FORWARD__FROZEN_TEST",
            "samples_total": int(report.get("dataset", {}).get("rows") or 0),
            "samples_test_nonoverlap": int((report.get("aggregates", {}).get("V5R", {}).get("logistic_balanced", {}) or {}).get("test_rows") or 0),
            "report": report,
        }
        bridge._request("POST", "ml_training_reports_v1?on_conflict=bridge_id,scope,generated_at", json=payload,
                        headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--stocks", default=",".join(DEFAULT_STOCKS))
    p.add_argument("--end", default="")
    a = p.parse_args()
    now = datetime.now(CN_TZ)
    minute = now.hour * 60 + now.minute
    if now.weekday() < 5 and ((570 <= minute < 690) or (780 <= minute < 900)):
        print("[STOP] Historical walk-forward is blocked during market hours to protect QMT live capture.")
        return 3

    stocks = [x.strip().upper() for x in a.stocks.split(",") if x.strip()]
    end_d = date.today() if not a.end else datetime.strptime(a.end, "%Y%m%d").date()
    start_d = end_d - timedelta(days=max(14, int(a.days)))
    start, end = start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d")
    stamp = now.strftime("%Y%m%d_%H%M%S")
    run_root = OUT_ROOT / stamp
    dataset_root = run_root / "dataset"
    audit_root = run_root / "audit"
    run_root.mkdir(parents=True, exist_ok=True)

    print(f"AStock QMT L1 60s historical walk-forward {VERSION}")
    print("REFERENCE ONLY: historical accuracy can never auto-promote or deploy a model.")
    print("If accuracy is extremely high at non-trivial coverage, treat it as a leakage alarm first.")
    try:
        from xtquant import xtdata
    except Exception as exc:
        print(f"[FAIL] xtquant import: {exc}")
        return 2

    report: Dict[str, Any] = {
        "version": VERSION, "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "start": start, "end": end, "stocks": stocks, "benchmarks": BENCHMARKS,
        "orders_placed": False, "live_runtime_stopped": False,
        "qualification": "HISTORICAL_REFERENCE_ONLY_NOT_PRISTINE_OOS",
        "architecture_lookahead_risk": True,
        "architecture_lookahead_note": "V4R/V5R design decisions used information from Sep-02..Sep-04; historical replay cannot be final prospective evidence.",
        "a_share_t_plus_1_note": "ask->bid +60s is signal-quality diagnostics, not a legal cash-stock 60s roundtrip PnL.",
        "anti_overfit_policy": {
            "chronological_walk_forward": True, "test_threshold_tuning": False,
            "session_isolation": True, "historical_benchmark_only": True,
            "future_feature_scan": True, "label_shuffle_control": True,
            "no_auto_promotion": True, "no_auto_deployment": True,
        },
    }

    v3_mod = original_expand = None
    try:
        report["dataset"] = _build_dataset(xtdata, stocks, start, end, dataset_root)
        dates = report["dataset"]["trade_dates"]
        # Five forward folds when 10 days exist: first test after five complete history days.
        start_idx = 5 if len(dates) >= 6 else len(dates) - 1
        test_indices = list(range(start_idx, len(dates)))
        if len(test_indices) > 5:
            test_indices = test_indices[-5:]

        v3_mod, original_expand = _session_safe_expand_patch()
        folds = []
        for i, idx in enumerate(test_indices, 1):
            fold_dates = dates[: idx + 1]
            folds.append(_run_fold(dataset_root, audit_root, fold_dates, i))
        report["folds"] = folds
        aggregates: Dict[str, Any] = {"V4R": {}, "V5R": {}}
        for variant in ("V4R", "V5R"):
            for family in ("logistic_balanced", "hist_gradient_boosting"):
                aggregates[variant][family] = _aggregate(folds, variant, family)
        report["aggregates"] = aggregates
        report["null_control"] = _null_control(dataset_root, dates)

        # Historical success must not become promotion. Future unseen days are mandatory.
        v5log = aggregates["V5R"]["logistic_balanced"]
        report["historical_reference_gate"] = {
            "directional_n_ge_100": int(v5log.get("directional_predictions") or 0) >= 100,
            "coverage_ge_5pct": float(v5log.get("directional_coverage_pct") or 0.0) >= 5.0,
            "net_edge_positive": float(v5log.get("avg_net_edge_bp") or -1e9) > 0.0,
            "positive_days_ge_3": int(v5log.get("positive_net_edge_days") or 0) >= 3,
            "wilson_lower_ge_52pct": float(v5log.get("accuracy_wilson_95_lower_pct") or 0.0) >= 52.0,
            "note": "passing this gate means historical reference is interesting, not that the model is deployable",
        }
        report["eligible_for_champion_promotion"] = False
        report["eligible_for_live_deployment"] = False
    except Exception as exc:
        report["fatal_error"] = f"{type(exc).__name__}: {exc}"
        print(f"[FAIL] {report['fatal_error']}")
    finally:
        if v3_mod is not None and original_expand is not None:
            v3_mod._expand = original_expand

    path = run_root / "walkforward_report.json"
    report["cloud_sync"] = _sync_cloud(report)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = OUT_ROOT / "latest.json"; latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[REPORT] {path}")
    if report.get("fatal_error"):
        return 1
    v5 = report.get("aggregates", {}).get("V5R", {}).get("logistic_balanced", {})
    print("[RESULT] V5R Logistic historical reference")
    print(f"  accuracy={v5.get('directional_accuracy_pct')}% n={v5.get('directional_predictions')} coverage={v5.get('directional_coverage_pct')}%")
    print(f"  net_edge={v5.get('avg_net_edge_bp')}bp exact_up_ask_to_bid={v5.get('avg_exact_ask_to_bid_60_edge_bp')}bp")
    print(f"  Wilson95 lower={v5.get('accuracy_wilson_95_lower_pct')}% positive_days={v5.get('positive_net_edge_days')}/{v5.get('folds')}")
    print("[IMPORTANT] This is historical reference only. Sep-07+ prospective days remain the real qualification test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
