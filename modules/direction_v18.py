# -*- coding: utf-8 -*-
"""V18 60/120-second direction fusion.

Design rules:
- the 1-minute direction remains visible whenever live data are ready;
- condition agreement is NOT presented as historical accuracy;
- only true Level-2 + >=90% agreement can receive the high-confidence label;
- the short-horizon engine stays separate from the swing/setup engine so daily
  context cannot overpower real-time microstructure.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from modules.short_direction import analyze_short_direction


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def _clip(value: float, bound: float = 1.0) -> float:
    return max(-bound, min(bound, float(value)))


def _component(value: float, scale: float, weight: float) -> float:
    if scale <= 0:
        return 0.0
    return _clip(value / scale) * weight


def _label(direction: str, tier: str, l2: bool) -> str:
    cn = "偏涨" if direction == "UP" else "偏跌"
    if tier == "高置信":
        return f"{cn}｜L2高置信"
    if tier == "中等":
        return f"{cn}｜中等"
    return f"{cn}｜弱"


def _direction_from_score(score: float, fallback: float = 0.0) -> str:
    if score > 0:
        return "UP"
    if score < 0:
        return "DOWN"
    return "UP" if fallback >= 0 else "DOWN"


def analyze_direction_v18(ticks: pd.DataFrame, l2: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base = analyze_short_direction(ticks)
    out = dict(base)
    out["metrics"] = dict(base.get("metrics", {}))
    l2 = l2 or {}
    true_l2 = bool(l2.get("ok"))
    out["signal_source"] = "QMT Level-2真实订单流" if true_l2 else "QMT Tick + 五档盘口降级"
    out["l2_confirmed"] = False
    out["high_confidence"] = False
    out["confidence_tier"] = "等待"
    out["l2_summary"] = l2 if true_l2 else {}

    # Keep V17's hard safety gates. Outside continuous trading / stale data / too
    # little coverage, we do not manufacture a direction.
    blocked_labels = {"休市", "数据延迟", "数据补齐中", "等待实时数据"}
    if not base.get("live") or str(base.get("label_60")) in blocked_labels:
        return out

    m = out["metrics"]
    r30 = _f(m.get("change_30s_pct"))
    r60 = _f(m.get("change_60s_pct"))
    r120 = _f(m.get("change_120s_pct"))
    vwap_dist = _f(m.get("above_vwap_pct"))
    micro = _f(m.get("microprice_bias_pct"))

    contributions: List[Tuple[str, float]] = []
    contributions.append(("30秒价格", _component(r30, 0.08, 1.0)))
    contributions.append(("60秒价格", _component(r60, 0.12, 1.0)))

    reasons_up: List[str] = []
    reasons_down: List[str] = []

    if true_l2:
        lm = l2.get("metrics", {}) or {}
        active = _f(lm.get("active_buy_pct"), 50.0)
        big = _f(lm.get("big_buy_pct"), 50.0)
        l2_agreement = _f(l2.get("agreement"), 0.0)
        l2_direction = str(l2.get("direction") or "WATCH")
        l2_signed = 0.0
        if l2_direction == "UP":
            l2_signed = l2_agreement / 100.0
        elif l2_direction == "DOWN":
            l2_signed = -l2_agreement / 100.0

        contributions.append(("真实主动成交", _component(active - 50.0, 18.0, 2.0)))
        contributions.append(("大/特大单", _component(big - 50.0, 18.0, 2.0)))
        contributions.append(("L2综合", l2_signed * 2.0))

        if active >= 55:
            reasons_up.append(f"真实主动买入{active:.0f}%")
        elif active <= 45:
            reasons_down.append(f"真实主动卖出{100-active:.0f}%")
        if big >= 55:
            reasons_up.append(f"大/特大单买入{big:.0f}%")
        elif big <= 45:
            reasons_down.append(f"大/特大单卖出{100-big:.0f}%")
        if l2_direction == "UP":
            reasons_up.extend(l2.get("reasons", [])[:2])
        elif l2_direction == "DOWN":
            reasons_down.extend(l2.get("reasons", [])[:2])
    else:
        buy_pct = _f(m.get("buy_pct"), 50.0)
        book = _f(m.get("buy_pressure_pct"), 50.0)
        contributions.append(("估算主动成交", _component(buy_pct - 50.0, 20.0, 1.5)))
        contributions.append(("五档盘口", _component(book - 50.0, 20.0, 1.0)))
        active = buy_pct
        big = 50.0
        l2_agreement = 0.0
        l2_direction = "WATCH"
        if buy_pct >= 55:
            reasons_up.append(f"主动买入估算{buy_pct:.0f}%")
        elif buy_pct <= 45:
            reasons_down.append(f"主动卖出估算{100-buy_pct:.0f}%")

    # Small tie-breakers only; they cannot dominate real order flow.
    contributions.append(("短VWAP", _component(vwap_dist, 0.15, 0.5)))
    contributions.append(("Microprice", _component(micro, 0.05, 0.5)))

    signed = float(sum(v for _, v in contributions))
    evidence = float(sum(abs(v) for _, v in contributions))
    fallback = r30 + 0.5 * r60 + 0.2 * _f(base.get("score")) / 100.0
    direction = _direction_from_score(signed, fallback=fallback)

    # 50 means perfectly conflicted / little evidence; 100 means all usable
    # evidence points to the same side. This remains a CONDITION score.
    if evidence <= 1e-9:
        agreement = 50
    else:
        agreement = int(round(50.0 + 50.0 * min(1.0, abs(signed) / evidence)))

    dominant_evidence = abs(signed)
    if true_l2 and agreement >= 90 and dominant_evidence >= 4.0:
        tier = "高置信"
        high_conf = True
    elif agreement >= 72 and dominant_evidence >= 2.0:
        tier = "中等"
        high_conf = False
    else:
        tier = "弱"
        high_conf = False

    out["direction_60"] = direction
    out["label_60"] = _label(direction, tier, true_l2)
    out["condition_agreement"] = agreement
    out["signal_strength"] = agreement
    out["confidence_tier"] = tier
    out["high_confidence"] = high_conf
    out["l2_confirmed"] = bool(high_conf and true_l2)
    out["level"] = "绿色" if direction == "UP" and high_conf else "红色" if direction == "DOWN" and high_conf else "灰色"

    if direction == "UP":
        out["reasons"] = list(dict.fromkeys(reasons_up + [f"近30秒{r30:+.2f}%", f"近60秒{r60:+.2f}%"]))[:3]
        out["alert"] = "1分钟方向偏涨" + ("，真实Level-2高一致确认" if high_conf else "，置信度尚未达到高置信门槛")
    else:
        out["reasons"] = list(dict.fromkeys(reasons_down + [f"近30秒{r30:+.2f}%", f"近60秒{r60:+.2f}%"]))[:3]
        out["alert"] = "1分钟方向偏跌" + ("，真实Level-2高一致确认" if high_conf else "，置信度尚未达到高置信门槛")

    # 120s direction is deliberately slower: blend the 60s microstructure score
    # with the observed 120s path. It is never allowed to upgrade 60s confidence.
    long_signed = signed + _component(r120, 0.20, 1.5)
    direction120 = _direction_from_score(long_signed, fallback=r120)
    out["direction_120"] = direction120
    out["label_120"] = ("偏涨" if direction120 == "UP" else "偏跌") + ("｜中等" if agreement >= 72 else "｜弱")

    out["metrics"].update({
        "direction_signed_score": signed,
        "direction_evidence": evidence,
        "l2_active_buy_pct": active,
        "l2_big_buy_pct": big,
        "l2_agreement": l2_agreement,
        "l2_direction": l2_direction,
        "true_l2": true_l2,
        "component_scores": {name: round(value, 4) for name, value in contributions},
    })
    return out
