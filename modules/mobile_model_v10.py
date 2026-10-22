"""Exact Python port of the phone model ``mobile-v10-exchange-window-prospective``.

Source of truth: supabase/functions/astock-mobile/market.ts (scoreCore,
stableModel, scoreView) and tick-window.ts. tests/test_mobile_v10_parity.py
runs both implementations on the same tick streams and requires identical
scores. Used for offline evaluation of the model the phone actually displays;
it never feeds live signals.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

MAX_GAP_SECONDS = 12
UP_MIN, DOWN_MAX, MEDIUM, STRONG = 15, -15, 40, 70


def js_round(x: float) -> int:
    """JavaScript Math.round (halves round up, toward +infinity)."""
    return int(math.floor(x + 0.5))


def num(value: Any, default: float = 0.0) -> float:
    if value is None:
        return 0.0          # Number(null) === 0 in JavaScript
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def arr(value: Any) -> List[float]:
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for v in value:
        x = num(v, float("nan")) if v is not None else 0.0
        if math.isfinite(x):
            out.append(x)
    return out


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def norm(x: float, s: float) -> float:
    return clamp(x / s, -1.0, 1.0) if s > 0 else 0.0


def exchange_time(tick: Dict[str, Any]) -> float:
    raw = num((tick or {}).get("time"), float("nan")) if (tick or {}).get("time") is not None else float("nan")
    if math.isfinite(raw) and raw > 1e12:
        return raw
    if math.isfinite(raw) and raw > 1e9:
        return raw * 1000
    tag = str((tick or {}).get("timetag") or "")
    if tag.endswith("Z") or (len(tag) > 6 and tag[-6] in "+-" and tag[-3] == ":"):
        try:
            return datetime.fromisoformat(tag.replace("Z", "+00:00")).timestamp() * 1000
        except ValueError:
            return 0.0
    return 0.0


def session_key(ts_ms: float) -> Optional[str]:
    if not ts_ms:
        return None
    d = datetime.fromtimestamp(ts_ms / 1000, timezone.utc) + timedelta(hours=8)
    if d.weekday() >= 5:
        return None
    minute = d.hour * 60 + d.minute
    session = "AM" if 570 <= minute < 690 else "PM" if 780 <= minute < 900 else None
    return f"{d.date().isoformat()}/{session}" if session else None


def valid_quote(tick: Dict[str, Any]) -> bool:
    bp, ap = tick.get("bidPrice"), tick.get("askPrice")
    bid = bp[0] if isinstance(bp, list) and bp else tick.get("bidPrice1")
    ask = ap[0] if isinstance(ap, list) and ap else tick.get("askPrice1")
    try:
        price, bid, ask = float(tick.get("lastPrice")), float(bid), float(ask)
    except (TypeError, ValueError):
        return False
    return all(math.isfinite(v) for v in (price, bid, ask)) and price > 0 and bid > 0 and ask >= bid


def prepare_ticks(ticks_in: List[Dict[str, Any]]) -> Dict[str, Any]:
    ticks: List[Dict[str, Any]] = []
    invalid = duplicates = resets = 0
    last_time = 0.0
    for tick in ticks_in or []:
        ts = exchange_time(tick)
        session = session_key(ts)
        if not ts or not session or not valid_quote(tick) or ts < last_time:
            invalid += 1
            continue
        if ticks and ts == last_time:
            ticks[-1] = tick
            duplicates += 1
            continue
        previous = ticks[-1] if ticks else None
        if previous is not None and (session_key(last_time) != session or ts - last_time > MAX_GAP_SECONDS * 1000
                                     or num(tick.get("volume"), float("nan")) < num(previous.get("volume"), float("nan"))
                                     or num(tick.get("amount"), float("nan")) < num(previous.get("amount"), float("nan"))):
            ticks = []
            resets += 1
        ticks.append(tick)
        last_time = ts
    return {"ticks": ticks, "invalid_rows": invalid, "duplicate_timestamps": duplicates,
            "window_resets": resets, "latest_valid_exchange_time": last_time}


def time_window(ticks: List[Dict[str, Any]], seconds: float) -> List[Dict[str, Any]]:
    if not ticks:
        return []
    end = exchange_time(ticks[-1])
    return [t for t in ticks if 0 < exchange_time(t) <= end and exchange_time(t) >= end - seconds * 1000]


def historical_cutoff(ticks: List[Dict[str, Any]], seconds_ago: float) -> List[Dict[str, Any]]:
    if not ticks:
        return []
    cutoff = exchange_time(ticks[-1]) - seconds_ago * 1000
    return [t for t in ticks if exchange_time(t) <= cutoff]


def complete_window(ticks: List[Dict[str, Any]], seconds: int) -> bool:
    if not ticks:
        return False
    span = (exchange_time(ticks[-1]) - exchange_time(ticks[0])) / 1000
    return span >= seconds and len(time_window(ticks, seconds)) >= (40 if seconds == 120 else 20)


def _best(t: Dict[str, Any]) -> Dict[str, float]:
    bp, ap, bv, av = arr(t.get("bidPrice")), arr(t.get("askPrice")), arr(t.get("bidVol")), arr(t.get("askVol"))
    pick = lambda lst, alt: lst[0] if lst else t.get(alt)
    return {"bid1": num(pick(bp, "bidPrice1"), 0), "ask1": num(pick(ap, "askPrice1"), 0),
            "bv1": num(pick(bv, "bidVol1"), 0), "av1": num(pick(av, "askVol1"), 0)}


WEIGHTS = (1.0, 0.78, 0.58, 0.40, 0.25)


def _book_pressure(t: Dict[str, Any]) -> float:
    bv, av = arr(t.get("bidVol"))[:5], arr(t.get("askVol"))[:5]
    b = sum((bv[i] if i < len(bv) else 0) * WEIGHTS[i] for i in range(5))
    a = sum((av[i] if i < len(av) else 0) * WEIGHTS[i] for i in range(5))
    return 100 * b / (b + a) if b + a > 0 else 50.0


def _avg_book(ticks, seconds) -> float:
    p = time_window(ticks, seconds)
    return sum(_book_pressure(t) for t in p) / len(p) if p else 50.0


def _momentum(ticks, seconds) -> float:
    p = time_window(ticks, seconds)
    if len(p) < 2:
        return 0.0
    p0, p1 = num(p[0].get("lastPrice"), 0), num(p[-1].get("lastPrice"), 0)
    return (p1 / p0 - 1) * 100 if p0 > 0 and p1 > 0 else 0.0


def _recent_vwap(ticks, seconds=60) -> float:
    p = time_window(ticks, seconds)
    if len(p) < 2:
        return 0.0
    dv = max(0.0, num(p[-1].get("volume"), 0) - num(p[0].get("volume"), 0))
    da = max(0.0, num(p[-1].get("amount"), 0) - num(p[0].get("amount"), 0))
    return da / (dv * 100) if dv > 0 and da > 0 else 0.0


def _trade_flow(ticks, seconds) -> Dict[str, float]:
    p = time_window(ticks, seconds)
    buy = sell = 0.0
    events, prev_sign = 0, 0
    for i in range(1, len(p)):
        dv = max(0.0, num(p[i].get("volume"), 0) - num(p[i - 1].get("volume"), 0))
        if dv <= 0:
            continue
        events += 1
        price, prev_price, q = num(p[i].get("lastPrice"), 0), num(p[i - 1].get("lastPrice"), 0), _best(p[i - 1])
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
    tot = buy + sell
    return {"buy_pct": 100 * buy / tot if tot > 0 else 50.0, "events": events}


def score_core(ticks: List[Dict[str, Any]]) -> Dict[str, Any]:
    latest = ticks[-1] if ticks else {}
    price = num(latest.get("lastPrice"), 0)
    r10, r30, r60, r120 = (_momentum(ticks, s) for s in (10, 30, 60, 120))
    q = _best(latest)
    spread_pct = (q["ask1"] - q["bid1"]) / price * 100 if price > 0 and q["ask1"] >= q["bid1"] and q["bid1"] > 0 else 0.0
    noise = max(0.02, spread_pct * 1.5)
    buy_pressure = _avg_book(ticks, 12)
    prior_window, recent = time_window(ticks, 35), time_window(ticks, 12)
    prior = prior_window[:len(prior_window) - len(recent)] if len(prior_window) > len(recent) else prior_window
    prior_pressure = sum(_book_pressure(t) for t in prior) / len(prior) if prior else buy_pressure
    pressure_change = buy_pressure - prior_pressure
    flow20, flow60 = _trade_flow(ticks, 20), _trade_flow(ticks, 60)
    event_conf = clamp(flow60["events"] / 8, 0, 1)
    rvwap = _recent_vwap(ticks, 60)
    above_vwap = (price / rvwap - 1) * 100 if price > 0 and rvwap > 0 else 0.0
    micro = 0.0
    if q["bid1"] > 0 and q["ask1"] > 0 and q["bv1"] + q["av1"] > 0 and price > 0:
        mp = (q["ask1"] * q["bv1"] + q["bid1"] * q["av1"]) / (q["bv1"] + q["av1"])
        micro = (mp / price - 1) * 100
    m60 = 25 * (0.20 * norm(r10, max(0.06, noise * 1.8)) + 0.35 * norm(r30, max(0.10, noise * 3))
                + 0.45 * norm(r60, max(0.16, noise * 4.5)))
    m120 = 25 * (0.25 * norm(r30, max(0.12, noise * 3.2)) + 0.35 * norm(r60, max(0.20, noise * 5))
                 + 0.40 * norm(r120, max(0.30, noise * 7)))
    f20, f60 = norm(flow20["buy_pct"] - 50, 25), norm(flow60["buy_pct"] - 50, 25)
    fg60 = 25 * event_conf * (0.65 * f20 + 0.35 * f60)
    fg120 = 25 * event_conf * (0.35 * f20 + 0.65 * f60)
    bl, bc = norm(buy_pressure - 50, 25), norm(pressure_change, 20)
    book60, book120 = 15 * (0.70 * bl + 0.30 * bc), 15 * (0.65 * bl + 0.35 * bc)
    vs = norm(above_vwap, max(0.10, noise * 3.5)) if rvwap > 0 else 0.0
    ms = norm(micro, max(0.03, noise * 0.75))
    loc60, loc120 = 15 * (0.75 * vs + 0.25 * ms), 10 * (0.85 * vs + 0.15 * ms)
    ex60 = ex120 = 0.0
    stretch = max(0.18, noise * 5)
    if r60 >= max(0.12, noise * 4) and r30 <= r60 * 0.45 and r10 <= 0:
        ex60 -= 6
    elif r60 <= -max(0.12, noise * 4) and r30 >= r60 * 0.45 and r10 >= 0:
        ex60 += 6
    if r120 >= max(0.18, noise * 6) and r60 <= r120 * 0.50 and r30 <= 0:
        ex120 -= 6
    elif r120 <= -max(0.18, noise * 6) and r60 >= r120 * 0.50 and r30 >= 0:
        ex120 += 6
    if max(r30, r60) > noise * 2 and flow60["buy_pct"] >= 55 and flow20["buy_pct"] <= flow60["buy_pct"] - 15:
        ex60 -= 8; ex120 -= 6
    elif min(r30, r60) < -noise * 2 and flow60["buy_pct"] <= 45 and flow20["buy_pct"] >= flow60["buy_pct"] + 15:
        ex60 += 8; ex120 += 6
    if r30 > noise * 1.5 and (pressure_change <= -12 or buy_pressure <= 42):
        ex60 -= 5; ex120 -= 4
    elif r30 < -noise * 1.5 and (pressure_change >= 12 or buy_pressure >= 58):
        ex60 += 5; ex120 += 4
    if above_vwap >= stretch and r10 <= 0:
        ex60 -= 5; ex120 -= 4
    elif above_vwap <= -stretch and r10 >= 0:
        ex60 += 5; ex120 += 4
    ex60, ex120 = clamp(ex60, -20, 20), clamp(ex120, -18, 18)
    groups60 = {"momentum": m60, "flow": fg60, "book": book60, "location": loc60, "exhaustion": ex60}
    groups120 = {"momentum": m120, "flow": fg120, "book": book120, "location": loc120, "exhaustion": ex120}
    return {"price": price, "score60_core": js_round(clamp(sum(groups60.values()), -100, 100)),
            "score120_core": js_round(clamp(sum(groups120.values()), -100, 100)),
            "groups60": groups60, "groups120": groups120, "r10": r10, "r30": r30, "r60": r60, "r120": r120,
            "spread_pct": spread_pct}


def _median(values: List[float]) -> float:
    xs = sorted(values)
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def stable_model(ticks_in: List[Dict[str, Any]]) -> Dict[str, Any]:
    health = prepare_ticks(ticks_in)
    ticks = health["ticks"]
    current = score_core(ticks)

    def samples(horizon: int, offsets) -> List[Optional[int]]:
        out = []
        for offset in offsets:
            part = historical_cutoff(ticks, offset)
            out.append(score_core(part)[f"score{horizon}_core"] if complete_window(part, horizon) else None)
        return out

    def result(values: List[Optional[int]]) -> Dict[str, Any]:
        valid = [v for v in values if v is not None]
        up = sum(1 for v in valid if v >= UP_MIN)
        down = sum(1 for v in valid if v <= DOWN_MAX)
        ready = len(valid) == 4
        score = js_round(_median(valid)) if ready else 0
        if (score >= 15 and up < 3) or (score <= -15 and down < 3):
            score = 0
        return {"score": score, "ready": ready}

    a = result(samples(60, (0, 5, 10, 15)))
    b = result(samples(120, (0, 10, 20, 30)))
    return {**current, "score60": a["score"], "score120": b["score"], "ready60": a["ready"], "ready120": b["ready"],
            "valid_tick_count": len(ticks)}


def score_view(score: float) -> str:
    return "UP" if score >= UP_MIN else "DOWN" if score <= DOWN_MAX else "WATCH"
