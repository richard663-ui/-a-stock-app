# -*- coding: utf-8 -*-
"""Shadow 60s/120s model used only for continuous forward evaluation.

This mirrors the mobile selective model's short-horizon feature family without
changing the mobile trading output. It is intentionally versioned separately so
research results cannot be confused with production labels.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List

MODEL_VERSION = "research-shadow-v2-20260825"


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if x == x else default
    except Exception:
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _ts(row: Dict[str, Any]) -> float:
    raw = _f(row.get("time"), 0.0)
    if raw > 1e12:
        return raw / 1000.0
    text = str(row.get("captured_at") or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _window(rows: List[Dict[str, Any]], seconds: int) -> List[Dict[str, Any]]:
    if len(rows) < 2:
        return rows
    end = _ts(rows[-1])
    if end <= 0:
        return rows[-max(2, seconds):]
    out = [r for r in rows if _ts(r) > 0 and _ts(r) >= end - seconds]
    return out if len(out) >= 2 else rows[-max(2, seconds):]


def _momentum(rows: List[Dict[str, Any]], seconds: int) -> float:
    p = _window(rows, seconds)
    if len(p) < 2:
        return 0.0
    p0, p1 = _f(p[0].get("lastPrice")), _f(p[-1].get("lastPrice"))
    return (p1 / p0 - 1.0) * 100.0 if p0 > 0 and p1 > 0 else 0.0


def _arr(v: Any) -> List[float]:
    if not isinstance(v, (list, tuple)):
        return []
    out: List[float] = []
    for x in v:
        try:
            f = float(x)
            if f == f:
                out.append(f)
        except Exception:
            pass
    return out


def _best(row: Dict[str, Any]) -> Dict[str, float]:
    bp, ap = _arr(row.get("bidPrice")), _arr(row.get("askPrice"))
    bv, av = _arr(row.get("bidVol")), _arr(row.get("askVol"))
    return {
        "bid1": bp[0] if bp else _f(row.get("bidPrice1")),
        "ask1": ap[0] if ap else _f(row.get("askPrice1")),
        "bv1": bv[0] if bv else _f(row.get("bidVol1")),
        "av1": av[0] if av else _f(row.get("askVol1")),
    }


def _book_pressure(row: Dict[str, Any]) -> float:
    bv, av = _arr(row.get("bidVol"))[:5], _arr(row.get("askVol"))[:5]
    weights = (1.0, 0.78, 0.58, 0.40, 0.25)
    b = sum((bv[i] if i < len(bv) else 0.0) * weights[i] for i in range(5))
    a = sum((av[i] if i < len(av) else 0.0) * weights[i] for i in range(5))
    return 100.0 * b / (a + b) if a + b > 0 else 50.0


def _avg_book(rows: List[Dict[str, Any]], seconds: int) -> float:
    p = _window(rows, seconds)
    return sum(_book_pressure(r) for r in p) / len(p) if p else 50.0


def _recent_vwap(rows: List[Dict[str, Any]], seconds: int = 60) -> float:
    p = _window(rows, seconds)
    if len(p) < 2:
        return 0.0
    v0, v1 = _f(p[0].get("volume")), _f(p[-1].get("volume"))
    a0, a1 = _f(p[0].get("amount")), _f(p[-1].get("amount"))
    dv, da = max(0.0, v1 - v0), max(0.0, a1 - a0)
    return da / (dv * 100.0) if dv > 0 and da > 0 else 0.0


def _trade_flow(rows: List[Dict[str, Any]], seconds: int = 60) -> Dict[str, float]:
    p = _window(rows, seconds)
    buy = sell = 0.0
    events = 0
    prev_sign = 0
    for i in range(1, len(p)):
        dv = max(0.0, _f(p[i].get("volume")) - _f(p[i - 1].get("volume")))
        if dv <= 0:
            continue
        events += 1
        price, prev_price = _f(p[i].get("lastPrice")), _f(p[i - 1].get("lastPrice"))
        q = _best(p[i - 1])
        sign = 0
        if q["ask1"] > 0 and price >= q["ask1"]:
            sign = 1
        elif q["bid1"] > 0 and price <= q["bid1"]:
            sign = -1
        elif price > prev_price:
            sign = 1
        elif price < prev_price:
            sign = -1
        else:
            sign = prev_sign
        prev_sign = sign
        if sign > 0:
            buy += dv
        elif sign < 0:
            sell += dv
    total = buy + sell
    return {"buy_pct": 100.0 * buy / total if total > 0 else 50.0, "events": float(events)}


def _context_adjustment(raw: float, bias: float, max_abs: int) -> int:
    if abs(bias) < 0.25 or abs(raw) < 55:
        return 0
    rs = 1 if raw > 0 else -1 if raw < 0 else 0
    bs = 1 if bias > 0 else -1 if bias < 0 else 0
    if rs == 0 or bs == 0:
        return 0
    if rs == bs:
        strength = _clamp((abs(bias) - 0.25) / 0.75, 0.0, 1.0)
        return rs * round(max_abs * strength)
    if abs(bias) >= 0.55:
        strength = _clamp((abs(bias) - 0.55) / 0.45, 0.0, 1.0)
        return -rs * round(max_abs * 0.5 * strength)
    return 0


def score_rows(rows: Iterable[Dict[str, Any]], macd_bias: float = 0.0) -> Dict[str, Any]:
    ticks = list(rows)
    latest = ticks[-1] if ticks else {}
    price = _f(latest.get("lastPrice"), 0.0)
    r10, r30, r60, r120 = (_momentum(ticks, s) for s in (10, 30, 60, 120))
    q = _best(latest)
    spread_pct = ((q["ask1"] - q["bid1"]) / price * 100.0) if price > 0 and q["bid1"] > 0 and q["ask1"] >= q["bid1"] else 0.0
    noise = max(0.02, spread_pct * 1.5)
    buy_pressure = _avg_book(ticks, 12)
    prior_window = _window(ticks, 35)
    recent = _window(ticks, 12)
    prior = prior_window[:-len(recent)] if len(prior_window) > len(recent) else prior_window
    prior_pressure = sum(_book_pressure(r) for r in prior) / len(prior) if prior else buy_pressure
    pressure_change = buy_pressure - prior_pressure
    flow = _trade_flow(ticks, 60)
    rvwap = _recent_vwap(ticks, 60)
    above_vwap = (price / rvwap - 1.0) * 100.0 if price > 0 and rvwap > 0 else 0.0
    micro = 0.0
    if q["bid1"] > 0 and q["ask1"] > 0 and q["bv1"] + q["av1"] > 0 and price > 0:
        mp = (q["ask1"] * q["bv1"] + q["bid1"] * q["av1"]) / (q["bv1"] + q["av1"])
        micro = (mp / price - 1.0) * 100.0

    raw60 = 0
    up60 = down60 = 0
    if r10 >= noise: raw60 += 8; up60 += 1
    elif r10 <= -noise: raw60 -= 8; down60 += 1
    if r30 >= noise * 1.5: raw60 += 15; up60 += 1
    elif r30 <= -noise * 1.5: raw60 -= 15; down60 += 1
    if r60 >= noise * 2: raw60 += 17; up60 += 1
    elif r60 <= -noise * 2: raw60 -= 17; down60 += 1
    if flow["events"] >= 3 and flow["buy_pct"] >= 63: raw60 += 25; up60 += 1
    elif flow["events"] >= 3 and flow["buy_pct"] <= 37: raw60 -= 25; down60 += 1
    if buy_pressure >= 60: raw60 += 14; up60 += 1
    elif buy_pressure <= 40: raw60 -= 14; down60 += 1
    if rvwap > 0 and above_vwap >= noise: raw60 += 13; up60 += 1
    elif rvwap > 0 and above_vwap <= -noise: raw60 -= 13; down60 += 1
    if micro >= noise * 0.35 or pressure_change >= 6: raw60 += 8; up60 += 1
    elif micro <= -noise * 0.35 or pressure_change <= -6: raw60 -= 8; down60 += 1
    raw60 = int(round(_clamp(raw60, -100, 100)))

    raw120 = 0
    up120 = down120 = 0
    if r30 >= noise * 1.5: raw120 += 12; up120 += 1
    elif r30 <= -noise * 1.5: raw120 -= 12; down120 += 1
    if r60 >= noise * 2: raw120 += 18; up120 += 1
    elif r60 <= -noise * 2: raw120 -= 18; down120 += 1
    if r120 >= noise * 2.5: raw120 += 24; up120 += 1
    elif r120 <= -noise * 2.5: raw120 -= 24; down120 += 1
    if flow["events"] >= 3 and flow["buy_pct"] >= 60: raw120 += 20; up120 += 1
    elif flow["events"] >= 3 and flow["buy_pct"] <= 40: raw120 -= 20; down120 += 1
    if buy_pressure >= 57: raw120 += 12; up120 += 1
    elif buy_pressure <= 43: raw120 -= 12; down120 += 1
    if rvwap > 0 and above_vwap >= noise: raw120 += 8; up120 += 1
    elif rvwap > 0 and above_vwap <= -noise: raw120 -= 8; down120 += 1
    if micro >= noise * 0.35 or pressure_change >= 6: raw120 += 6; up120 += 1
    elif micro <= -noise * 0.35 or pressure_change <= -6: raw120 -= 6; down120 += 1
    raw120 = int(round(_clamp(raw120, -100, 100)))

    adj60 = _context_adjustment(raw60, macd_bias, 6)
    adj120 = _context_adjustment(raw120, macd_bias, 9)
    score60 = int(round(_clamp(raw60 + adj60, -100, 100)))
    score120 = int(round(_clamp(raw120 + adj120, -100, 100)))
    return {
        "price": price, "score60": score60, "score120": score120,
        "score60_raw": raw60, "score120_raw": raw120,
        "up60": up60, "down60": down60, "up120": up120, "down120": down120,
        "r10": r10, "r30": r30, "r60": r60, "r120": r120,
        "buy_pct": flow["buy_pct"], "flow_events": int(flow["events"]),
        "buy_pressure_pct": buy_pressure, "pressure_change_pct": pressure_change,
        "recent_vwap": rvwap, "above_vwap_pct": above_vwap,
        "microprice_bias_pct": micro, "noise_pct": noise,
        "macd_bias": macd_bias, "macd_adjustment_60": adj60, "macd_adjustment_120": adj120,
    }


def score_label(score: int) -> Dict[str, Any]:
    s = int(score)
    if s >= 70: return {"direction": "UP", "tier": "STRONG_UP"}
    if s >= 40: return {"direction": "UP", "tier": "MEDIUM_UP"}
    if s >= 15: return {"direction": "UP", "tier": "LIGHT_UP"}
    if s <= -70: return {"direction": "DOWN", "tier": "STRONG_DOWN"}
    if s <= -40: return {"direction": "DOWN", "tier": "MEDIUM_DOWN"}
    if s <= -15: return {"direction": "DOWN", "tier": "LIGHT_DOWN"}
    return {"direction": "WATCH", "tier": "NEUTRAL"}


def high_confidence(horizon: int, metrics: Dict[str, Any]) -> bool:
    if horizon == 60:
        return metrics["score60"] >= 90 and metrics["up60"] >= 5 and metrics["down60"] == 0 or metrics["score60"] <= -90 and metrics["down60"] >= 5 and metrics["up60"] == 0
    return metrics["score120"] >= 82 and metrics["up120"] >= 5 and metrics["down120"] <= 1 or metrics["score120"] <= -82 and metrics["down120"] >= 5 and metrics["up120"] <= 1
