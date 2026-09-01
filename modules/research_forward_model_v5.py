# -*- coding: utf-8 -*-
"""V5A research model: V4b direction + adaptive 60s selective gate + confidence.

The 60-second direction remains the primary research target. The model does not
use a hand-tuned parameter table for individual stocks. Instead it estimates a
symbol-local recent movement scale from the incoming tick history and filters
weak/conflicted candidates to WATCH. MACD remains confidence context only.
"""
from __future__ import annotations

from bisect import bisect_right
from typing import Any, Dict, Iterable, List, Tuple

import modules.research_forward_model_v4b as v4b

MODEL_VERSION = "research-shadow-v5a-adaptive-normalized-selective-60s"


def _f(v: Any, d: float = 0.0) -> float:
    try:
        x = float(v)
        return x if x == x else d
    except Exception:
        return d


def _clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, float(x)))


def _state(tf: Dict[str, Any]):
    if not isinstance(tf, dict) or not tf.get("ok"):
        return None
    return _clamp(_f(tf.get("state_score")) / 100.0, -1, 1)


def _transition(tf: Dict[str, Any]):
    if not isinstance(tf, dict) or not tf.get("ok"):
        return None
    return _clamp(_f(tf.get("transition_score")), -1, 1)


def _weighted_context(ctx: Dict[str, Any], direction: int, transition: bool = False) -> float:
    weights = {"m1": .45, "m5": .35, "m15": .20} if transition else {"m1": .24, "m5": .30, "m15": .24, "m30": .12, "m60": .07, "day": .02, "week": .01}
    tfs = (ctx or {}).get("timeframes") or {}
    s = w = 0.0
    for k, wt in weights.items():
        v = _transition(tfs.get(k) or {}) if transition else _state(tfs.get(k) or {})
        if v is None:
            continue
        s += wt * v
        w += wt
    return _clamp(direction * s / w, -1, 1) if w else 0.0


def _group_alignment(groups: Dict[str, Any], direction: int) -> float:
    caps = {"momentum": 25.0, "flow": 25.0, "book": 15.0, "location": 15.0}
    s = w = 0.0
    for k, cap in caps.items():
        v = groups.get(k)
        if v is None:
            continue
        s += cap * _clamp(direction * _f(v) / cap, -1, 1)
        w += cap
    return _clamp(s / w, -1, 1) if w else 0.0


def _label(c: int) -> str:
    if c >= 85:
        return "TOP_CANDIDATE_UNVALIDATED"
    if c >= 75:
        return "STRONG"
    if c >= 60:
        return "GOOD"
    if c >= 45:
        return "MEDIUM"
    return "LOW"


def _row_ts(row: Dict[str, Any]) -> float:
    raw = _f(row.get("time"), 0.0)
    if raw > 1e12:
        return raw / 1000.0
    text = str(row.get("captured_at") or "")
    if text:
        try:
            from datetime import datetime
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    return 0.0


def _quantile(values: List[float], q: float, default: float) -> float:
    xs = sorted(float(x) for x in values if x == x and x >= 0)
    if not xs:
        return default
    pos = _clamp(q, 0.0, 1.0) * (len(xs) - 1)
    lo = int(pos)
    hi = min(len(xs) - 1, lo + 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _adaptive_price_scales(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    points: List[Tuple[float, float]] = []
    for row in rows:
        ts, price = _row_ts(row), _f(row.get("lastPrice"), 0.0)
        if ts > 0 and price > 0:
            if points and ts == points[-1][0]:
                points[-1] = (ts, price)
            else:
                points.append((ts, price))
    if len(points) < 12:
        return {"move30_pct": 0.08, "move60_pct": 0.12, "samples": 0}
    end = points[-1][0]
    points = [p for p in points if p[0] >= end - 900.0]
    times = [p[0] for p in points]
    prices = [p[1] for p in points]
    step = max(1, len(points) // 240)
    out: Dict[str, float] = {"move30_pct": 0.08, "move60_pct": 0.12, "samples": float(len(points))}
    for horizon, key, floor in ((30.0, "move30_pct", 0.06), (60.0, "move60_pct", 0.10)):
        moves: List[float] = []
        for i in range(0, len(points), step):
            target = times[i] - horizon
            j = bisect_right(times, target) - 1
            if j < 0 or j >= i:
                continue
            elapsed = times[i] - times[j]
            if elapsed < horizon * 0.65 or elapsed > horizon * 1.75:
                continue
            p0, p1 = prices[j], prices[i]
            if p0 > 0 and p1 > 0:
                moves.append(abs(p1 / p0 - 1.0) * 100.0)
        out[key] = max(floor, _quantile(moves, 0.65, floor))
    return out


def confidence(metrics: Dict[str, Any], ctx: Dict[str, Any], horizon: int) -> Dict[str, Any]:
    score = int(metrics["score60"] if horizon == 60 else metrics["score120"])
    direction = 1 if score >= 15 else -1 if score <= -15 else 0
    if direction == 0:
        return {"confidence_score": 0, "confidence_tier": "NEUTRAL", "calibrated_probability": False, "empirical": False}
    support_obj = metrics.get("direction_support_60" if horizon == 60 else "direction_support_120") or {}
    support = _f(support_obj.get("up" if direction > 0 else "down")) / 4.0
    dispersion = _f(metrics.get("score60_dispersion" if horizon == 60 else "score120_dispersion"), 100.0)
    dispersion_quality = 1.0 - _clamp(dispersion / (70.0 if horizon == 120 else 55.0), 0, 1)
    strength = _clamp(abs(score) / 40.0, 0, 1)
    groups = metrics.get("groups60" if horizon == 60 else "groups120") or {}
    g_align = _group_alignment(groups, direction)
    m_align = _weighted_context(ctx, direction, False)
    t_align = _weighted_context(ctx, direction, True)
    ex = _f(metrics.get("exhaustion_60" if horizon == 60 else "exhaustion_120"))
    risk = _clamp(-direction * ex / (18.0 if horizon == 120 else 20.0), 0, 1)
    c = 30 + 10*strength + 16*support + 10*dispersion_quality + 14*g_align + 16*m_align + 6*t_align - 16*risk
    age = max(0.0, __import__("time").time() - _f((ctx or {}).get("fetched_ts"), 0.0))
    if age > 120:
        c -= 6
    c = int(round(_clamp(c, 0, 100)))
    return {
        "confidence_score": c, "confidence_tier": _label(c),
        "direction_strength_pct": round(strength*100, 1),
        "persistence_pct": round(support*100, 1),
        "dispersion_quality_pct": round(dispersion_quality*100, 1),
        "group_alignment": round(g_align, 4),
        "macd_alignment": round(m_align, 4),
        "macd_transition_alignment": round(t_align, 4),
        "exhaustion_risk": round(risk, 4),
        "macd_context_score": _f((ctx or {}).get("score")),
        "macd_context_age_seconds": round(age, 2) if age < 1e6 else None,
        "calibrated_probability": False, "empirical": False,
    }


def _selective_60s_gate(metrics: Dict[str, Any], structural_confidence: Dict[str, Any], scales: Dict[str, float]) -> Dict[str, Any]:
    score = int(metrics.get("score60") or 0)
    if abs(score) < 15:
        return {"pass": False, "reasons": ["neutral_score"], "price_abnormality": 0.0, "threshold": 20}
    direction = 1 if score > 0 else -1
    groups = metrics.get("groups60") or {}
    aligned = sum(1 for k in ("momentum", "flow", "book", "location") if direction * _f(groups.get(k)) > 1.5)
    opposed = sum(1 for k in ("momentum", "flow", "book", "location") if direction * _f(groups.get(k)) < -1.5)
    scale30 = max(0.06, _f(scales.get("move30_pct"), 0.08))
    scale60 = max(0.10, _f(scales.get("move60_pct"), 0.12))
    r30 = abs(_f(metrics.get("r30"))) / scale30
    r60 = abs(_f(metrics.get("r60"))) / scale60
    price_abnormality = 0.40 * r30 + 0.60 * r60
    confidence_score = int(_f(structural_confidence.get("confidence_score"), 0.0))
    threshold = 20
    if scale60 >= 0.25:
        threshold += 2
    if scale60 >= 0.40:
        threshold += 2
    reasons: List[str] = []
    if abs(score) < threshold:
        reasons.append("score_below_adaptive_threshold")
    if aligned < 2:
        reasons.append("insufficient_independent_groups")
    if opposed > 1:
        reasons.append("too_many_opposed_groups")
    if confidence_score < 50:
        reasons.append("low_structural_confidence")
    if price_abnormality < 0.45 and aligned < 3:
        reasons.append("ordinary_price_state_without_broad_support")
    return {
        "pass": not reasons,
        "reasons": reasons,
        "threshold": threshold,
        "aligned_groups": aligned,
        "opposed_groups": opposed,
        "price_abnormality": round(price_abnormality, 4),
        "scale30_pct": round(scale30, 6),
        "scale60_pct": round(scale60, 6),
        "structural_confidence": confidence_score,
    }


def score_rows(rows: Iterable[Dict[str, Any]], macd_context: Any = None) -> Dict[str, Any]:
    tick_rows = list(rows)
    out = v4b.score_rows(tick_rows, 0.0)
    ctx = macd_context if isinstance(macd_context, dict) else {}
    scales = _adaptive_price_scales(tick_rows)

    candidate_score60 = int(out.get("score60") or 0)
    c60_candidate = confidence(out, ctx, 60)
    gate60 = _selective_60s_gate(out, c60_candidate, scales)
    if candidate_score60 and not gate60["pass"]:
        out["score60"] = 0

    c60 = confidence(out, ctx, 60)
    c120 = confidence(out, ctx, 120)
    out.update({
        "score60_candidate": candidate_score60,
        "selective_gate_60": bool(gate60["pass"]),
        "selective_gate_reasons_60": list(gate60.get("reasons") or []),
        "adaptive_scale_30_pct": gate60.get("scale30_pct", scales.get("move30_pct")),
        "adaptive_scale_60_pct": gate60.get("scale60_pct", scales.get("move60_pct")),
        "price_abnormality_60": gate60.get("price_abnormality"),
        "adaptive_score_threshold_60": gate60.get("threshold"),
        "candidate_confidence_60": c60_candidate.get("confidence_score"),
        "confidence_60": c60["confidence_score"], "confidence_120": c120["confidence_score"],
        "confidence_tier_60": c60["confidence_tier"], "confidence_tier_120": c120["confidence_tier"],
        "confidence_components_60": c60, "confidence_components_120": c120,
        "macd_calibration_score": (ctx or {}).get("score"),
        "macd_calibration_summary": (ctx or {}).get("summary"),
        "macd_calibration_resonance": (ctx or {}).get("resonance"),
        "macd_calibration_timeframes": (ctx or {}).get("timeframes") or {},
        "macd_calibration_version": (ctx or {}).get("calibration_version"),
        "model_version": MODEL_VERSION,
    })
    return out


def score_label(score: int) -> Dict[str, Any]:
    return v4b.score_label(score)


def high_confidence(horizon: int, metrics: Dict[str, Any]) -> bool:
    return False
