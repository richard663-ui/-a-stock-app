# -*- coding: utf-8 -*-
"""Setup grading + intraday VWAP state for the V18 dashboard.

This is intentionally a small, auditable rule engine. It does not claim to be
SMB Capital's proprietary ASSET framework. It implements the public, general
idea of separating swing/context quality from intraday location/timing.

Important: returned scores are engineering baselines, not probabilities. They
must be calibrated with walk-forward/out-of-sample data before being treated as
statistical edge.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def _parse_ts(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype="datetime64[ns]")
    raw = df.get("captured_at", pd.Series(index=df.index, dtype=object))
    out = pd.to_datetime(raw, errors="coerce")
    if out.notna().any():
        return out
    return pd.to_datetime(df.get("timetag", pd.Series(index=df.index, dtype=object)), errors="coerce")


def _intraday_vwap_series(ticks: pd.DataFrame) -> pd.Series:
    """QMT A-share stock volume is treated as lots; one lot = 100 shares."""
    if ticks is None or ticks.empty:
        return pd.Series(dtype=float)
    amount = pd.to_numeric(ticks.get("amount"), errors="coerce")
    volume = pd.to_numeric(ticks.get("volume"), errors="coerce")
    denom = volume * 100.0
    vwap = amount / denom.replace(0, np.nan)
    return vwap.where((vwap > 0) & np.isfinite(vwap))


def _window(df: pd.DataFrame, seconds: int) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    work["_ts"] = _parse_ts(work)
    if work["_ts"].notna().sum() >= 2:
        work = work.sort_values("_ts")
        end = work["_ts"].dropna().iloc[-1]
        part = work[work["_ts"] >= end - pd.Timedelta(seconds=seconds)]
        if len(part) >= 2:
            return part
    return work.tail(max(2, min(len(work), seconds)))


def analyze_vwap_state(ticks: pd.DataFrame) -> Dict[str, Any]:
    base = {
        "ok": False,
        "vwap": 0.0,
        "distance_pct": 0.0,
        "slope_60s_pct": 0.0,
        "state": "等待VWAP",
        "location_score": 50,
        "reasons": [],
    }
    if ticks is None or ticks.empty:
        return base

    work = ticks.copy().reset_index(drop=True)
    price = pd.to_numeric(work.get("lastPrice"), errors="coerce")
    vwap_series = _intraday_vwap_series(work)
    valid = price.notna() & vwap_series.notna() & (price > 0) & (vwap_series > 0)
    if valid.sum() < 2:
        return base

    last_idx = valid[valid].index[-1]
    last = float(price.loc[last_idx])
    vwap = float(vwap_series.loc[last_idx])
    distance = (last / vwap - 1.0) * 100.0

    part = _window(work.loc[:last_idx], 60)
    part_vwap = _intraday_vwap_series(part).dropna()
    slope = 0.0
    if len(part_vwap) >= 2 and float(part_vwap.iloc[0]) > 0:
        slope = (float(part_vwap.iloc[-1]) / float(part_vwap.iloc[0]) - 1.0) * 100.0

    prev_part = _window(work.loc[:last_idx], 30)
    prev_price = pd.to_numeric(prev_part.get("lastPrice"), errors="coerce")
    prev_vwap = _intraday_vwap_series(prev_part)
    prev_dist = pd.Series(dtype=float)
    if not prev_part.empty:
        prev_dist = (prev_price / prev_vwap - 1.0) * 100.0
        prev_dist = prev_dist.replace([np.inf, -np.inf], np.nan).dropna()

    reclaimed = bool(len(prev_dist) >= 2 and prev_dist.min() < -0.05 and distance >= 0.0)
    lost = bool(len(prev_dist) >= 2 and prev_dist.max() > 0.05 and distance <= 0.0)
    near = abs(distance) <= 0.18
    stretched = abs(distance) >= 1.50

    score = 50
    reasons = []
    if distance > 0:
        score += 12
        reasons.append("价格在日内VWAP上方")
    elif distance < 0:
        score -= 12
        reasons.append("价格在日内VWAP下方")

    if slope >= 0.02:
        score += 10
        reasons.append("VWAP近60秒向上")
    elif slope <= -0.02:
        score -= 10
        reasons.append("VWAP近60秒向下")

    if reclaimed:
        score += 12
        state = "收复VWAP"
        reasons.append("刚从VWAP下方收复")
    elif lost:
        score -= 12
        state = "跌破VWAP"
        reasons.append("刚从VWAP上方跌破")
    elif distance >= 0 and slope >= 0:
        state = "VWAP上方偏强"
    elif distance <= 0 and slope <= 0:
        state = "VWAP下方偏弱"
    elif near:
        state = "VWAP附近博弈"
    else:
        state = "VWAP混合"

    if stretched:
        score += -8 if distance > 0 else 8
        reasons.append("距离VWAP过远，追价风险上升")

    score = int(max(0, min(100, round(score))))
    return {
        "ok": True,
        "vwap": vwap,
        "distance_pct": distance,
        "slope_60s_pct": slope,
        "state": state,
        "location_score": score,
        "reclaimed": reclaimed,
        "lost": lost,
        "near_vwap": near,
        "stretched": stretched,
        "reasons": reasons[:4],
    }


def _macd_bias(macd: Dict[str, str]) -> Tuple[int, str]:
    label = str(macd.get("label") or "")
    if "金叉" in label or "多头" in label:
        return 8, label
    if "死叉" in label or "走弱" in label:
        return -8, label
    if "修复" in label:
        return 4, label
    return 0, label or "MACD中性"


def grade_setup(
    *,
    qishi: Dict[str, Any],
    macd: Dict[str, str],
    catalyst: Dict[str, Any],
    vwap: Dict[str, Any],
    short_metrics: Dict[str, Any],
    l2_summary: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return A/B/C/D setup grade from four mostly independent families.

    Families:
      1) swing context/trend
      2) intraday VWAP location
      3) real-time order flow
      4) catalyst/risk

    The fixed weights are a conservative starting specification and should be
    frozen until enough out-of-sample data exist to recalibrate them.
    """
    l2_summary = l2_summary or {}
    reasons = []

    # 1) Swing context: 0..35.
    q_score = _f(qishi.get("latest_score"), 0.0) if qishi.get("ok") else 0.0
    context = min(27.0, max(0.0, q_score * 0.27))
    macd_adj, macd_label = _macd_bias(macd)
    context = max(0.0, min(35.0, context + macd_adj))
    if q_score >= 70:
        reasons.append("日线趋势/起势较强")
    elif q_score < 45:
        reasons.append("日线趋势尚弱")
    reasons.append(f"MACD：{macd_label}")

    # 2) VWAP location: 0..25.
    location = _f(vwap.get("location_score"), 50.0) * 0.25
    if vwap.get("ok"):
        reasons.append(f"VWAP：{vwap.get('state')}")

    # 3) Order flow: 0..30. Prefer true Level-2.
    flow = 15.0
    true_l2 = bool(l2_summary.get("ok"))
    if true_l2:
        lm = l2_summary.get("metrics", {}) or {}
        active = _f(lm.get("active_buy_pct"), 50.0)
        big = _f(lm.get("big_buy_pct"), 50.0)
        l2_agreement = _f(l2_summary.get("agreement"), 50.0)
        direction = str(l2_summary.get("direction") or "WATCH")
        flow = 15.0 + (active - 50.0) * 0.16 + (big - 50.0) * 0.10
        if direction == "UP":
            flow += max(0.0, (l2_agreement - 50.0) * 0.08)
        elif direction == "DOWN":
            flow -= max(0.0, (l2_agreement - 50.0) * 0.08)
        reasons.append(f"真实L2主动买入 {active:.0f}%")
    else:
        buy_pct = _f(short_metrics.get("buy_pct"), 50.0)
        book = _f(short_metrics.get("buy_pressure_pct"), 50.0)
        flow = 15.0 + (buy_pct - 50.0) * 0.18 + (book - 50.0) * 0.10
        reasons.append("Level-2不足，暂用Tick/盘口降级")
    flow = max(0.0, min(30.0, flow))

    # 4) Catalyst/risk: 0..10.
    cat = _f(catalyst.get("score"), 0.0)
    event_risk = min(10.0, max(0.0, cat * 0.10))
    risk_state = str(qishi.get("risk_state") or "")
    if "高位" in risk_state or "高风险" in risk_state:
        event_risk = max(0.0, event_risk - 5.0)
        reasons.append("位置/风险偏高")
    elif "风险可控" in risk_state:
        event_risk = min(10.0, event_risk + 2.0)

    total = int(round(max(0.0, min(100.0, context + location + flow + event_risk))))
    if total >= 80:
        grade, label = "A", "高质量Setup"
    elif total >= 65:
        grade, label = "B", "可交易候选"
    elif total >= 50:
        grade, label = "C", "等待确认"
    else:
        grade, label = "D", "放弃/回避"

    distance = _f(vwap.get("distance_pct"), 0.0)
    flow_ok = flow >= 18.0
    if grade in {"A", "B"} and vwap.get("ok") and distance >= -0.15 and flow_ok:
        trade_state = "可交易候选"
    elif grade == "D" or (vwap.get("ok") and vwap.get("lost") and flow < 12):
        trade_state = "放弃"
    else:
        trade_state = "等待确认"

    return {
        "score": total,
        "grade": grade,
        "label": label,
        "trade_state": trade_state,
        "true_l2": true_l2,
        "components": {
            "context": round(context, 1),
            "vwap": round(location, 1),
            "flow": round(flow, 1),
            "catalyst_risk": round(event_risk, 1),
        },
        "reasons": reasons[:5],
        "baseline_only": True,
    }
