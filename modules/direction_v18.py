# -*- coding: utf-8 -*-
"""V18 60/120-second direction fusion.

The 1-minute direction remains the primary target. V9 adaptive mode keeps the
same microstructure factors but normalizes price components by each symbol's
recent realized movement and suppresses weak/conflicted candidates to WATCH.
Condition agreement is not historical accuracy. A high-confidence label still
requires core Level-2 feed coverage.
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
    return 0.0 if scale <= 0 else _clip(value / scale) * weight


def _label(direction: str, tier: str) -> str:
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


def _core_l2_ready(l2: Dict[str, Any]) -> bool:
    """High confidence needs trades plus at least two independent L2 contexts."""
    available = l2.get("available", {}) or {}
    if not available.get("l2transaction"):
        return False
    secondary = sum(bool(available.get(name)) for name in (
        "l2quote", "l2order", "l2quoteaux", "l2transactioncount", "l2orderqueue"
    ))
    return secondary >= 2


def _realized_move_scales(ticks: pd.DataFrame) -> Dict[str, float]:
    """Robust symbol-local 30s/60s absolute-move scales from recent ticks."""
    out = {"move30_pct": 0.08, "move60_pct": 0.12, "samples": 0}
    if ticks is None or ticks.empty:
        return out
    try:
        frame = ticks.copy()
        ts = pd.to_datetime(frame.get("captured_at"), errors="coerce")
        if ts.isna().all():
            ts = pd.to_datetime(frame.get("timetag"), errors="coerce")
        price = pd.to_numeric(frame.get("lastPrice"), errors="coerce")
        good = pd.DataFrame({"ts": ts, "price": price}).dropna()
        good = good[good["price"] > 0].sort_values("ts")
        if len(good) < 12:
            return out
        end = good["ts"].iloc[-1]
        good = good[good["ts"] >= end - pd.Timedelta(minutes=15)]
        series = good.set_index("ts")["price"]
        series = series[~series.index.duplicated(keep="last")].sort_index()
        grid = series.resample("5s").last().ffill().dropna()
        if len(grid) < 12:
            return out
        out["samples"] = int(len(grid))
        for seconds, key, floor in ((30, "move30_pct", 0.06), (60, "move60_pct", 0.10)):
            periods = max(1, seconds // 5)
            moves = grid.pct_change(periods=periods).abs().dropna() * 100.0
            if len(moves) >= 6:
                q = float(moves.quantile(0.65))
                if np.isfinite(q) and q > 0:
                    out[key] = max(floor, q)
    except Exception:
        return out
    return out


def analyze_direction_v18(ticks: pd.DataFrame, l2: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base = analyze_short_direction(ticks)
    out = dict(base)
    out["metrics"] = dict(base.get("metrics", {}))
    l2 = l2 or {}
    any_l2 = bool(l2.get("ok"))
    core_l2 = bool(any_l2 and _core_l2_ready(l2))
    out["signal_source"] = "QMT Level-2真实订单流" if any_l2 else "QMT Tick + 五档盘口降级"
    out["l2_confirmed"] = False
    out["high_confidence"] = False
    out["confidence_tier"] = "等待"
    out["l2_summary"] = l2 if any_l2 else {}

    blocked_labels = {"休市", "数据延迟", "数据补齐中", "等待实时数据"}
    if not base.get("live") or str(base.get("label_60")) in blocked_labels:
        out["metrics"].update({"true_l2": any_l2, "core_l2_ready": core_l2})
        return out

    m = out["metrics"]
    r30 = _f(m.get("change_30s_pct"))
    r60 = _f(m.get("change_60s_pct"))
    r120 = _f(m.get("change_120s_pct"))
    vwap_dist = _f(m.get("above_vwap_pct"))
    micro = _f(m.get("microprice_bias_pct"))
    spread = max(0.0, _f(m.get("spread_pct")))

    realized = _realized_move_scales(ticks)
    scale30 = max(0.06, _f(realized.get("move30_pct"), 0.08), spread * 2.0)
    scale60 = max(0.10, _f(realized.get("move60_pct"), 0.12), spread * 3.0)
    vwap_scale = max(0.08, min(0.30, 0.65 * scale60))
    micro_scale = max(0.02, min(0.10, max(spread * 1.25, 0.20 * scale30)))

    contributions: List[Tuple[str, float]] = [
        ("30秒价格", _component(r30, scale30, 1.0)),
        ("60秒价格", _component(r60, scale60, 1.0)),
    ]
    reasons_up: List[str] = []
    reasons_down: List[str] = []

    if any_l2:
        lm = l2.get("metrics", {}) or {}
        active = _f(lm.get("active_buy_pct"), 50.0)
        big = _f(lm.get("big_buy_pct"), 50.0)
        l2_agreement = _f(l2.get("agreement"), 0.0)
        l2_direction = str(l2.get("direction") or "WATCH")
        l2_signed = l2_agreement / 100.0 if l2_direction == "UP" else -l2_agreement / 100.0 if l2_direction == "DOWN" else 0.0

        contributions.extend([
            ("真实主动成交", _component(active - 50.0, 18.0, 2.0)),
            ("大/特大单", _component(big - 50.0, 18.0, 2.0)),
            ("L2综合", l2_signed * 2.0),
        ])

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
        contributions.extend([
            ("估算主动成交", _component(buy_pct - 50.0, 20.0, 1.5)),
            ("五档盘口", _component(book - 50.0, 20.0, 1.0)),
        ])
        active, big = buy_pct, 50.0
        l2_agreement, l2_direction = 0.0, "WATCH"
        if buy_pct >= 55:
            reasons_up.append(f"主动买入估算{buy_pct:.0f}%")
        elif buy_pct <= 45:
            reasons_down.append(f"主动卖出估算{100-buy_pct:.0f}%")

    contributions.extend([
        ("短VWAP", _component(vwap_dist, vwap_scale, 0.5)),
        ("Microprice", _component(micro, micro_scale, 0.5)),
    ])

    signed = float(sum(v for _, v in contributions))
    evidence = float(sum(abs(v) for _, v in contributions))
    fallback = r30 / scale30 + 0.5 * r60 / scale60 + 0.2 * _f(base.get("score")) / 100.0
    raw_direction = _direction_from_score(signed, fallback=fallback)
    sign = 1.0 if raw_direction == "UP" else -1.0
    aligned_components = sum(1 for _, value in contributions if sign * value >= 0.25)
    opposed_components = sum(1 for _, value in contributions if sign * value <= -0.25)
    agreement = 50 if evidence <= 1e-9 else int(round(50.0 + 50.0 * min(1.0, abs(signed) / evidence)))
    dominant_evidence = abs(signed)

    if core_l2 and agreement >= 90 and dominant_evidence >= 4.0 and aligned_components >= 4 and opposed_components <= 1:
        tier, high_conf = "高置信", True
    elif agreement >= 72 and dominant_evidence >= 2.0 and aligned_components >= 3 and opposed_components <= 1:
        tier, high_conf = "中等", False
    else:
        tier, high_conf = "弱", False

    selective_pass = tier != "弱"
    direction = raw_direction if selective_pass else "WATCH"
    out["direction_60"] = direction
    out["label_60"] = _label(raw_direction, tier) if selective_pass else "震荡｜观望"
    out["condition_agreement"] = agreement
    out["signal_strength"] = agreement if selective_pass else 0
    out["confidence_tier"] = tier
    out["high_confidence"] = high_conf
    out["l2_confirmed"] = bool(high_conf and core_l2)
    out["level"] = "绿色" if direction == "UP" and high_conf else "红色" if direction == "DOWN" and high_conf else "灰色"

    if not selective_pass:
        out["reasons"] = [
            f"候选方向{raw_direction}",
            f"同向证据{aligned_components}项/反向{opposed_components}项",
            "弱信号已过滤，等待更清晰的1分钟机会",
        ]
        out["alert"] = "1分钟候选信号不足，自动WATCH，避免低质量频繁出手"
    elif direction == "UP":
        out["reasons"] = list(dict.fromkeys(reasons_up + [f"近30秒{r30:+.2f}%", f"近60秒{r60:+.2f}%"]))[:3]
        out["alert"] = "1分钟方向偏涨" + ("，核心Level-2高一致确认" if high_conf else "，已通过自适应筛选")
    else:
        out["reasons"] = list(dict.fromkeys(reasons_down + [f"近30秒{r30:+.2f}%", f"近60秒{r60:+.2f}%"]))[:3]
        out["alert"] = "1分钟方向偏跌" + ("，核心Level-2高一致确认" if high_conf else "，已通过自适应筛选")

    long_signed = signed + _component(r120, max(0.16, scale60 * 1.6), 1.5)
    raw_direction120 = _direction_from_score(long_signed, fallback=r120)
    out["direction_120"] = raw_direction120 if selective_pass else "WATCH"
    out["label_120"] = (("偏涨" if raw_direction120 == "UP" else "偏跌") + "｜中等") if selective_pass else "震荡｜观望"

    out["metrics"].update({
        "direction_signed_score": signed,
        "direction_evidence": evidence,
        "raw_direction_60": raw_direction,
        "selective_gate_60": selective_pass,
        "aligned_components_60": aligned_components,
        "opposed_components_60": opposed_components,
        "adaptive_scale_30_pct": round(scale30, 6),
        "adaptive_scale_60_pct": round(scale60, 6),
        "adaptive_vwap_scale_pct": round(vwap_scale, 6),
        "adaptive_micro_scale_pct": round(micro_scale, 6),
        "adaptive_scale_samples": int(_f(realized.get("samples"), 0)),
        "l2_active_buy_pct": active,
        "l2_big_buy_pct": big,
        "l2_agreement": l2_agreement,
        "l2_direction": l2_direction,
        "true_l2": any_l2,
        "core_l2_ready": core_l2,
        "component_scores": {name: round(value, 4) for name, value in contributions},
    })
    return out
