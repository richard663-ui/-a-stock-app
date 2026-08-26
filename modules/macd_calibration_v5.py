# -*- coding: utf-8 -*-
"""Minute-K MACD structure for V5 confidence calibration.

MACD is context for confidence, not a hard trading gate and not the direction
engine itself. Uses public Tencent K-lines and short TTL caching.
"""
from __future__ import annotations
import math
import threading
import time
from datetime import datetime
from typing import Any, Dict
import requests

CACHE_TTL = 25.0
_TIMEOUT = 4.2
_lock = threading.Lock()
_cache: Dict[str, Dict[str, Any]] = {}


def _f(v, d=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except Exception:
        return d


def _clamp(x, a, b):
    return max(a, min(b, float(x)))


def _tx_symbol(symbol: str) -> str:
    code = symbol[:6]
    if symbol.endswith(".SH"):
        return "sh" + code
    if symbol.endswith(".BJ"):
        return "bj" + code
    return "sz" + code


def _parse(raw):
    out = []
    for x in raw or []:
        if not isinstance(x, list) or len(x) < 6:
            continue
        o, c, h, l, v = _f(x[1], math.nan), _f(x[2], math.nan), _f(x[3], math.nan), _f(x[4], math.nan), _f(x[5])
        if all(math.isfinite(z) for z in (o, c, h, l)) and c > 0:
            out.append({"time": str(x[0] or ""), "open": o, "close": c, "high": h, "low": l, "volume": v})
    return out


def _get_json(url):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"}, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _fetch_period(symbol: str, tf: str, count: int):
    tx = _tx_symbol(symbol)
    if tf == "day":
        p = f"{tx},day,,,{count},qfq"
        j = _get_json("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=" + p)
        box = (j.get("data") or {}).get(tx) or {}
        return _parse(box.get("qfqday") or box.get("day") or [])
    key = "m" + tf
    p = f"{tx},{key},,{count}"
    j = _get_json("https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=" + p)
    return _parse((((j.get("data") or {}).get(tx) or {}).get(key) or []))


def _ema(vals, span):
    if not vals:
        return []
    a = 2.0 / (span + 1.0)
    out = [vals[0]]
    for x in vals[1:]:
        out.append(a * x + (1.0 - a) * out[-1])
    return out


def _sign(x):
    return 1 if x > 0 else -1 if x < 0 else 0


def _build(rows, tf):
    if len(rows) < 35:
        return {"ok": False, "timeframe": tf, "label": "数据不足", "state_score": 0, "transition_score": 0, "bars": len(rows)}
    close = [r["close"] for r in rows]
    e12, e26 = _ema(close, 12), _ema(close, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = _ema(dif, 9)
    hist = [2 * (d - a) for d, a in zip(dif, dea)]
    i = len(close) - 1
    d, a, h, hp, hpp = dif[i], dea[i], hist[i], hist[i-1], hist[i-2]
    rel = d - a
    scale = max(abs(d), abs(a), abs(h), abs(close[i]) * 0.0008, 1e-9)
    dif_slope, dea_slope = d - dif[i-1], a - dea[i-1]
    hist_delta, hist_accel = h - hp, (h - hp) - (hp - hpp)
    rel_sig = _clamp(rel / (scale * .55), -1, 1)
    hist_trend = _clamp(hist_delta / (scale * .45), -1, 1)
    dif_slope_sig = _clamp(dif_slope / (scale * .32), -1, 1)
    accel_sig = _clamp(hist_accel / (scale * .40), -1, 1)
    zero_sig = _clamp(d / (scale * 3.5), -1, 1)
    cross, age = "NONE", None
    for j in range(i, max(0, i-20), -1):
        s0, s1 = _sign(dif[j]-dea[j]), _sign(dif[j-1]-dea[j-1])
        if s0 and s1 and s0 != s1:
            cross, age = ("GOLDEN" if s0 > 0 else "DEAD"), i-j
            break
    cross_boost = 0.0
    if age is not None and age <= 3:
        cross_boost = (1 if cross == "GOLDEN" else -1) * (1 - age * .18)
    transition = _clamp(.45*hist_trend + .30*accel_sig + .25*cross_boost, -1, 1)
    state = _clamp(100*(.45*rel_sig + .20*hist_trend + .18*dif_slope_sig + .10*accel_sig + .07*zero_sig), -100, 100)
    near = abs(d) <= scale*.35 or abs(a) <= scale*.35
    zone = "水上" if d > 0 and a > 0 and not near else "水下" if d < 0 and a < 0 and not near else "零轴附近"
    hist_state = "红柱放大" if h > 0 and h > hp else "红柱缩短" if h > 0 else "绿柱放大" if h < 0 and abs(h) > abs(hp) else "绿柱缩短" if h < 0 else "柱体走平"
    label = f"{zone}·{hist_state}"
    if age is not None and age <= 3:
        label += f"·{'金叉' if cross == 'GOLDEN' else '死叉'}{'刚发生' if age == 0 else str(age)+'根K'}"
    return {
        "ok": True, "timeframe": tf, "label": label, "pattern": label, "zone": zone, "hist_state": hist_state,
        "dif": d, "dea": a, "hist": h, "dif_slope": dif_slope, "dea_slope": dea_slope,
        "hist_delta": hist_delta, "hist_accel": hist_accel, "cross": cross, "cross_age_bars": age,
        "state_score": round(state), "transition_score": round(transition, 4), "score": round(state), "bars": len(rows),
    }


def _week_key(s: str):
    try:
        d = datetime.fromisoformat(s[:10])
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    except Exception:
        return s[:10]


def _aggregate_week(rows):
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        k = _week_key(str(r["time"]))
        if k not in out:
            out[k] = {"time": k, "open": r["open"], "close": r["close"], "high": r["high"], "low": r["low"], "volume": r["volume"]}
        else:
            x = out[k]
            x["close"] = r["close"]; x["high"] = max(x["high"], r["high"]); x["low"] = min(x["low"], r["low"]); x["volume"] += r["volume"]
    return list(out.values())


def _fresh(symbol: str):
    rows = {}
    for tf, count in (("1", 240), ("5", 240), ("15", 220), ("30", 220), ("60", 220), ("day", 520)):
        try:
            rows[tf] = _fetch_period(symbol, tf, count)
        except Exception:
            rows[tf] = []
    day = rows["day"]
    tfs = {
        "m1": _build(rows["1"], "1分钟"), "m5": _build(rows["5"], "5分钟"),
        "m15": _build(rows["15"], "15分钟"), "m30": _build(rows["30"], "30分钟"),
        "m60": _build(rows["60"], "60分钟"), "day": _build(day, "日线"),
        "week": _build(_aggregate_week(day), "周线"),
    }
    weights = {"m1": .16, "m5": .24, "m15": .24, "m30": .16, "m60": .10, "day": .07, "week": .03}
    s = w = 0.0; pos = neg = 0
    for k, wt in weights.items():
        x = tfs[k]
        if x.get("ok"):
            z = _f(x.get("state_score"))
            s += z*wt; w += wt
            pos += z >= 25; neg += z <= -25
    score = s/w if w else 0.0
    return {
        "symbol": symbol, "source": "腾讯公开K线", "updated_at": datetime.now().astimezone().isoformat(),
        "fetched_ts": time.time(), "score": round(score), "bias_score": _clamp(score/100, -1, 1),
        "summary": "MACD多周期强多" if score >= 55 else "MACD多周期偏强" if score >= 20 else "MACD多周期强空" if score <= -55 else "MACD多周期偏弱" if score <= -20 else "MACD周期分化",
        "resonance": "多头共振" if pos >= 4 else "空头共振" if neg >= 4 else "周期分化",
        "timeframes": tfs, "calibration_version": "macd-structure-v9",
    }


def get_context(symbol: str):
    now = time.time()
    with _lock:
        c = _cache.get(symbol)
        if c and now - _f(c.get("fetched_ts")) <= CACHE_TTL:
            return dict(c)
    try:
        c = _fresh(symbol)
        with _lock:
            _cache[symbol] = c
        return dict(c)
    except Exception:
        with _lock:
            c = _cache.get(symbol)
        if c:
            out = dict(c); out["stale"] = True; return out
        return {"symbol": symbol, "score": 0, "bias_score": 0.0, "summary": "MACD不可用", "resonance": "--", "timeframes": {}, "fetched_ts": 0.0, "calibration_version": "macd-structure-v9"}
