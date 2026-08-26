# -*- coding: utf-8 -*-
"""Grouped/stabilized 60s/120s shadow model for forward evaluation.

V4 goals:
- stop double-counting correlated evidence by scoring factor groups with caps;
- add explicit exhaustion/reversal penalties (price-flow divergence, deceleration,
  book divergence and extreme VWAP stretch);
- give MACD regime a larger but still bounded role;
- stabilize displayed/research scores with short trailing medians instead of
  reacting to every single tick;
- do not call any score "high confidence" until empirical calibration proves it.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any, Dict, Iterable, List

MODEL_VERSION = "research-shadow-v4-grouped-stable-exhaustion"


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if x == x else default
    except Exception:
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def _norm(x: float, scale: float) -> float:
    return 0.0 if scale <= 0 else _clamp(float(x) / float(scale), -1.0, 1.0)


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


def _cutoff(rows: List[Dict[str, Any]], seconds_ago: int) -> List[Dict[str, Any]]:
    if seconds_ago <= 0 or len(rows) < 2:
        return rows
    end = _ts(rows[-1])
    if end <= 0:
        return rows[:-seconds_ago] if len(rows) > seconds_ago + 2 else rows
    cutoff_ts = end - seconds_ago
    out = [r for r in rows if 0 < _ts(r) <= cutoff_ts]
    return out if len(out) >= 10 else rows


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


def _trade_flow(rows: List[Dict[str, Any]], seconds: int) -> Dict[str, float]:
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
    return {
        "buy_pct": 100.0 * buy / total if total > 0 else 50.0,
        "events": float(events),
        "net_lots": buy - sell,
    }


def _group_sign_count(groups: Dict[str, float], score: float) -> int:
    if abs(score) < 1e-9:
        return 0
    sign = 1 if score > 0 else -1
    return sum(1 for k, v in groups.items() if k != "exhaustion" and v * sign > 1.5)


def _score_core(ticks: List[Dict[str, Any]], macd_bias: float) -> Dict[str, Any]:
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

    flow20 = _trade_flow(ticks, 20)
    flow60 = _trade_flow(ticks, 60)
    event_conf = _clamp(flow60["events"] / 8.0, 0.0, 1.0)

    rvwap = _recent_vwap(ticks, 60)
    above_vwap = (price / rvwap - 1.0) * 100.0 if price > 0 and rvwap > 0 else 0.0
    micro = 0.0
    if q["bid1"] > 0 and q["ask1"] > 0 and q["bv1"] + q["av1"] > 0 and price > 0:
        mp = (q["ask1"] * q["bv1"] + q["bid1"] * q["av1"]) / (q["bv1"] + q["av1"])
        micro = (mp / price - 1.0) * 100.0

    m60 = 25.0 * (
        0.20 * _norm(r10, max(0.06, noise * 1.8))
        + 0.35 * _norm(r30, max(0.10, noise * 3.0))
        + 0.45 * _norm(r60, max(0.16, noise * 4.5))
    )
    m120 = 25.0 * (
        0.25 * _norm(r30, max(0.12, noise * 3.2))
        + 0.35 * _norm(r60, max(0.20, noise * 5.0))
        + 0.40 * _norm(r120, max(0.30, noise * 7.0))
    )

    f20 = _norm(flow20["buy_pct"] - 50.0, 25.0)
    f60 = _norm(flow60["buy_pct"] - 50.0, 25.0)
    flow_group60 = 25.0 * event_conf * (0.65 * f20 + 0.35 * f60)
    flow_group120 = 25.0 * event_conf * (0.35 * f20 + 0.65 * f60)

    book_level = _norm(buy_pressure - 50.0, 25.0)
    book_change = _norm(pressure_change, 20.0)
    book60 = 15.0 * (0.70 * book_level + 0.30 * book_change)
    book120 = 15.0 * (0.65 * book_level + 0.35 * book_change)

    vwap_sig = _norm(above_vwap, max(0.10, noise * 3.5)) if rvwap > 0 else 0.0
    micro_sig = _norm(micro, max(0.03, noise * 0.75))
    loc60 = 15.0 * (0.75 * vwap_sig + 0.25 * micro_sig)
    loc120 = 10.0 * (0.85 * vwap_sig + 0.15 * micro_sig)

    regime = _clamp(macd_bias, -1.0, 1.0)
    regime60 = 10.0 * regime
    regime120 = 15.0 * regime

    ex60 = 0.0
    ex120 = 0.0
    stretch = max(0.18, noise * 5.0)

    if r60 >= max(0.12, noise * 4.0) and r30 <= r60 * 0.45 and r10 <= 0:
        ex60 -= 6.0
    elif r60 <= -max(0.12, noise * 4.0) and r30 >= r60 * 0.45 and r10 >= 0:
        ex60 += 6.0

    if r120 >= max(0.18, noise * 6.0) and r60 <= r120 * 0.50 and r30 <= 0:
        ex120 -= 6.0
    elif r120 <= -max(0.18, noise * 6.0) and r60 >= r120 * 0.50 and r30 >= 0:
        ex120 += 6.0

    if max(r30, r60) > noise * 2.0 and flow60["buy_pct"] >= 55 and flow20["buy_pct"] <= flow60["buy_pct"] - 15:
        ex60 -= 8.0; ex120 -= 6.0
    elif min(r30, r60) < -noise * 2.0 and flow60["buy_pct"] <= 45 and flow20["buy_pct"] >= flow60["buy_pct"] + 15:
        ex60 += 8.0; ex120 += 6.0

    if r30 > noise * 1.5 and (pressure_change <= -12 or buy_pressure <= 42):
        ex60 -= 5.0; ex120 -= 4.0
    elif r30 < -noise * 1.5 and (pressure_change >= 12 or buy_pressure >= 58):
        ex60 += 5.0; ex120 += 4.0

    if above_vwap >= stretch and r10 <= 0:
        ex60 -= 5.0; ex120 -= 4.0
    elif above_vwap <= -stretch and r10 >= 0:
        ex60 += 5.0; ex120 += 4.0

    ex60 = _clamp(ex60, -20.0, 20.0)
    ex120 = _clamp(ex120, -18.0, 18.0)

    groups60 = {
        "momentum": round(m60, 3), "flow": round(flow_group60, 3), "book": round(book60, 3),
        "location": round(loc60, 3), "regime": round(regime60, 3), "exhaustion": round(ex60, 3),
    }
    groups120 = {
        "momentum": round(m120, 3), "flow": round(flow_group120, 3), "book": round(book120, 3),
        "location": round(loc120, 3), "regime": round(regime120, 3), "exhaustion": round(ex120, 3),
    }
    raw60 = int(round(_clamp(sum(groups60.values()), -100, 100)))
    raw120 = int(round(_clamp(sum(groups120.values()), -100, 100)))

    return {
        "price": price,
        "score60_core": raw60, "score120_core": raw120,
        "groups60": groups60, "groups120": groups120,
        "group_agreement60": _group_sign_count(groups60, raw60),
        "group_agreement120": _group_sign_count(groups120, raw120),
        "r10": r10, "r30": r30, "r60": r60, "r120": r120,
        "buy_pct": flow60["buy_pct"], "buy_pct_20s": flow20["buy_pct"],
        "flow_events": int(flow60["events"]), "flow_event_confidence": event_conf,
        "buy_pressure_pct": buy_pressure, "pressure_change_pct": pressure_change,
        "recent_vwap": rvwap, "above_vwap_pct": above_vwap,
        "microprice_bias_pct": micro, "noise_pct": noise,
        "macd_bias": regime, "macd_regime_60": regime60, "macd_regime_120": regime120,
        "exhaustion_60": ex60, "exhaustion_120": ex120,
    }


def _stable_median(values: List[float], fallback: float) -> int:
    good = [float(v) for v in values if v == v]
    return int(round(median(good))) if good else int(round(fallback))


def score_rows(rows: Iterable[Dict[str, Any]], macd_bias: float = 0.0) -> Dict[str, Any]:
    ticks = list(rows)
    current = _score_core(ticks, macd_bias)

    scores60: List[int] = []
    scores120: List[int] = []
    for offset in (0, 5, 10, 15):
        part = _cutoff(ticks, offset)
        if len(part) >= 10:
            scores60.append(_score_core(part, macd_bias)["score60_core"])
    for offset in (0, 10, 20, 30):
        part = _cutoff(ticks, offset)
        if len(part) >= 10:
            scores120.append(_score_core(part, macd_bias)["score120_core"])

    score60 = _stable_median(scores60, current["score60_core"])
    score120 = _stable_median(scores120, current["score120_core"])
    dispersion60 = (max(scores60) - min(scores60)) if scores60 else 0
    dispersion120 = (max(scores120) - min(scores120)) if scores120 else 0

    out = dict(current)
    out.update({
        "score60": score60, "score120": score120,
        "score60_raw_current": current["score60_core"],
        "score120_raw_current": current["score120_core"],
        "score60_samples": scores60, "score120_samples": scores120,
        "score60_dispersion": dispersion60, "score120_dispersion": dispersion120,
        "stable_window_60s": 15, "stable_window_120s": 30,
        "calibrated_probability": False,
        "high_confidence_enabled": False,
        "model_version": MODEL_VERSION,
    })
    return out


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
    return False
