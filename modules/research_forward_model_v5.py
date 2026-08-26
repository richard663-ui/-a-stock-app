# -*- coding: utf-8 -*-
"""V5 research model: V4b direction + separate structural confidence calibration.

Direction stays based on microstructure/price action. MACD minute-K structure
only calibrates confidence; it is not a hard gate and does not decide direction.
"""
from __future__ import annotations
from typing import Any, Dict, Iterable

import modules.research_forward_model_v4b as v4b

MODEL_VERSION = "research-shadow-v5-direction-confidence-macd-structure"


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


def score_rows(rows: Iterable[Dict[str, Any]], macd_context: Any = None) -> Dict[str, Any]:
    out = v4b.score_rows(rows, 0.0)
    ctx = macd_context if isinstance(macd_context, dict) else {}
    c60 = confidence(out, ctx, 60)
    c120 = confidence(out, ctx, 120)
    out.update({
        "confidence_60": c60["confidence_score"], "confidence_120": c120["confidence_score"],
        "confidence_tier_60": c60["confidence_tier"], "confidence_tier_120": c120["confidence_tier"],
        "confidence_components_60": c60, "confidence_components_120": c120,
        "macd_calibration_summary": (ctx or {}).get("summary"),
        "macd_calibration_resonance": (ctx or {}).get("resonance"),
        "macd_calibration_version": (ctx or {}).get("calibration_version"),
        "model_version": MODEL_VERSION,
    })
    return out


def score_label(score: int) -> Dict[str, Any]:
    return v4b.score_label(score)


def high_confidence(horizon: int, metrics: Dict[str, Any]) -> bool:
    return False
