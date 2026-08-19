# -*- coding: utf-8 -*-
"""MACD golden-cross event study for A-share daily bars.

Research only. It does not place orders and it is intentionally separate from the
production dashboard. The goal is to answer empirically:
- Do underwater (below-zero) and above-water MACD golden crosses behave differently?
- How often are the next 1/2/3 sessions positive?
- What are next 1/2/3/5-day returns using a no-look-ahead entry at next open?
- Can a near-cross setup predict a golden cross on the following session?

Usage:
    py services/macd_event_study.py

Outputs:
    runtime/macd_event_study_events.csv
    runtime/macd_event_study_summary.csv
    runtime/macd_precross_summary.csv
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.data_sources import fetch_tencent_kline

RUNTIME = PROJECT_ROOT / "runtime"
RUNTIME.mkdir(exist_ok=True)

# Deliberately diversified rather than cherry-picked around one industry.
UNIVERSE: Dict[str, str] = {
    "000400": "许继电气",
    "600406": "国电南瑞",
    "601179": "中国西电",
    "600522": "中天科技",
    "300274": "阳光电源",
    "300750": "宁德时代",
    "002371": "北方华创",
    "600519": "贵州茅台",
    "600036": "招商银行",
    "000333": "美的集团",
}

HORIZONS = (1, 2, 3, 5)


def add_macd(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy().reset_index(drop=True)
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out["open"] = pd.to_numeric(out["open"], errors="coerce")
    ema12 = out["close"].ewm(span=12, adjust=False).mean()
    ema26 = out["close"].ewm(span=26, adjust=False).mean()
    out["dif"] = ema12 - ema26
    out["dea"] = out["dif"].ewm(span=9, adjust=False).mean()
    out["macd_gap"] = out["dif"] - out["dea"]
    out["macd_hist"] = 2.0 * out["macd_gap"]
    out["golden_cross"] = (out["macd_gap"] > 0) & (out["macd_gap"].shift(1) <= 0)
    out["death_cross"] = (out["macd_gap"] < 0) & (out["macd_gap"].shift(1) >= 0)
    out["gap_bp"] = np.where(out["close"] > 0, out["macd_gap"] / out["close"] * 10000.0, np.nan)
    return out


def cross_zone(row: pd.Series) -> str:
    dif, dea = float(row["dif"]), float(row["dea"])
    if dif < 0 and dea < 0:
        return "水下金叉"
    if dif > 0 and dea > 0:
        return "水上金叉"
    return "零轴附近"


def forward_event_rows(code: str, name: str, df: pd.DataFrame) -> List[dict]:
    rows: List[dict] = []
    idxs = df.index[df["golden_cross"].fillna(False)].tolist()
    for i in idxs:
        # Need t+1 open plus the requested exit horizon. Ignore events too close to sample end.
        if i + max(HORIZONS) >= len(df):
            continue
        entry = float(df.loc[i + 1, "open"])
        if not np.isfinite(entry) or entry <= 0:
            continue
        row = {
            "code": code,
            "name": name,
            "cross_date": str(df.loc[i, "date"]),
            "zone": cross_zone(df.loc[i]),
            "cross_close": float(df.loc[i, "close"]),
            "entry_date": str(df.loc[i + 1, "date"]),
            "entry_open": entry,
            "dif": float(df.loc[i, "dif"]),
            "dea": float(df.loc[i, "dea"]),
            "gap_bp": float(df.loc[i, "gap_bp"]),
        }
        for h in HORIZONS:
            exit_close = float(df.loc[i + h, "close"])
            row[f"ret_{h}d_pct"] = (exit_close / entry - 1.0) * 100.0

        # "Red for N days" means N consecutive higher closes after the cross day.
        up1 = bool(df.loc[i + 1, "close"] > df.loc[i, "close"])
        up2 = bool(df.loc[i + 2, "close"] > df.loc[i + 1, "close"])
        up3 = bool(df.loc[i + 3, "close"] > df.loc[i + 2, "close"])
        row["up_day1"] = up1
        row["up_2_consecutive"] = up1 and up2
        row["up_3_consecutive"] = up1 and up2 and up3

        # Maximum adverse/favourable excursion in the first 3 sessions from next-open entry.
        lows = pd.to_numeric(df.loc[i + 1:i + 3, "low"], errors="coerce")
        highs = pd.to_numeric(df.loc[i + 1:i + 3, "high"], errors="coerce")
        row["mae_3d_pct"] = (float(lows.min()) / entry - 1.0) * 100.0
        row["mfe_3d_pct"] = (float(highs.max()) / entry - 1.0) * 100.0
        rows.append(row)
    return rows


def precross_rows(code: str, name: str, df: pd.DataFrame) -> List[dict]:
    """Daily near-cross study.

    This is NOT a tradable previous-day-close signal because it uses the completed
    daily close. It measures whether a narrowing negative MACD gap tends to cross on
    the following session. A real previous-day entry must be evaluated using a
    pre-close intraday snapshot (e.g. 14:50 QMT data) to avoid look-ahead bias.
    """
    out: List[dict] = []
    gap = df["macd_gap"]
    # Conservative candidate: still below DEA, gap rising 3 sessions, and close to zero.
    candidate = (
        (gap < 0)
        & (gap > gap.shift(1))
        & (gap.shift(1) > gap.shift(2))
        & (df["gap_bp"] >= -15.0)
        & (df["dif"] < 0)
        & (df["dea"] < 0)
    )
    for i in df.index[candidate.fillna(False)].tolist():
        if i + 3 >= len(df):
            continue
        next_cross = bool(df.loc[i + 1, "golden_cross"])
        entry = float(df.loc[i + 1, "open"])
        if entry <= 0:
            continue
        out.append({
            "code": code,
            "name": name,
            "setup_date": str(df.loc[i, "date"]),
            "gap_bp": float(df.loc[i, "gap_bp"]),
            "next_day_golden_cross": next_cross,
            "ret_1d_pct": (float(df.loc[i + 1, "close"]) / entry - 1.0) * 100.0,
            "ret_2d_pct": (float(df.loc[i + 2, "close"]) / entry - 1.0) * 100.0,
            "ret_3d_pct": (float(df.loc[i + 3, "close"]) / entry - 1.0) * 100.0,
        })
    return out


def summarize_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    summaries: List[dict] = []
    for zone, part in events.groupby("zone", dropna=False):
        row = {
            "zone": zone,
            "events": len(part),
            "next_day_up_rate_pct": part["up_day1"].mean() * 100.0,
            "2_consecutive_up_rate_pct": part["up_2_consecutive"].mean() * 100.0,
            "3_consecutive_up_rate_pct": part["up_3_consecutive"].mean() * 100.0,
            "median_mae_3d_pct": part["mae_3d_pct"].median(),
            "median_mfe_3d_pct": part["mfe_3d_pct"].median(),
        }
        for h in HORIZONS:
            col = f"ret_{h}d_pct"
            row[f"mean_{h}d_pct"] = part[col].mean()
            row[f"median_{h}d_pct"] = part[col].median()
            row[f"win_{h}d_pct"] = (part[col] > 0).mean() * 100.0
        summaries.append(row)
    return pd.DataFrame(summaries)


def summarize_precross(pre: pd.DataFrame) -> pd.DataFrame:
    if pre.empty:
        return pd.DataFrame()
    return pd.DataFrame([{
        "setups": len(pre),
        "next_day_cross_precision_pct": pre["next_day_golden_cross"].mean() * 100.0,
        "mean_1d_pct": pre["ret_1d_pct"].mean(),
        "mean_2d_pct": pre["ret_2d_pct"].mean(),
        "mean_3d_pct": pre["ret_3d_pct"].mean(),
        "win_1d_pct": (pre["ret_1d_pct"] > 0).mean() * 100.0,
        "win_2d_pct": (pre["ret_2d_pct"] > 0).mean() * 100.0,
        "win_3d_pct": (pre["ret_3d_pct"] > 0).mean() * 100.0,
    }])


def main() -> int:
    all_events: List[dict] = []
    all_pre: List[dict] = []
    print("MACD 10-stock event study (research only)")
    for code, name in UNIVERSE.items():
        print(f"Fetching {code} {name} ...", end=" ", flush=True)
        df = fetch_tencent_kline(code, 1000)
        if df is None or df.empty or len(df) < 60:
            print("FAILED")
            continue
        df = add_macd(df)
        events = forward_event_rows(code, name, df)
        pre = precross_rows(code, name, df)
        all_events.extend(events)
        all_pre.extend(pre)
        print(f"OK bars={len(df)} crosses={len(events)} precross={len(pre)}")

    events_df = pd.DataFrame(all_events)
    pre_df = pd.DataFrame(all_pre)
    summary_df = summarize_events(events_df)
    pre_summary_df = summarize_precross(pre_df)

    events_path = RUNTIME / "macd_event_study_events.csv"
    summary_path = RUNTIME / "macd_event_study_summary.csv"
    pre_path = RUNTIME / "macd_precross_events.csv"
    pre_summary_path = RUNTIME / "macd_precross_summary.csv"
    events_df.to_csv(events_path, index=False, encoding="utf-8-sig")
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    pre_df.to_csv(pre_path, index=False, encoding="utf-8-sig")
    pre_summary_df.to_csv(pre_summary_path, index=False, encoding="utf-8-sig")

    print("\n=== CONFIRMED GOLDEN CROSS ===")
    if summary_df.empty:
        print("No usable events.")
    else:
        print(summary_df.round(2).to_string(index=False))

    print("\n=== UNDERWATER NEAR-CROSS SETUP ===")
    if pre_summary_df.empty:
        print("No usable setups.")
    else:
        print(pre_summary_df.round(2).to_string(index=False))

    print(f"\nEvents:  {events_path}")
    print(f"Summary: {summary_path}")
    print(f"Pre-cross summary: {pre_summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
