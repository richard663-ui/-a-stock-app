# -*- coding: utf-8 -*-
"""V18 direction fusion: price/tick engine + real Level-2 confirmation."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from modules.short_direction import analyze_short_direction


def analyze_direction_v18(ticks: pd.DataFrame, l2: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base = analyze_short_direction(ticks)
    l2 = l2 or {}
    if not l2.get("ok"):
        base["signal_source"] = "QMT Tick + 五档盘口"
        base["l2_confirmed"] = False
        return base

    out = dict(base)
    out["metrics"] = dict(base.get("metrics", {}))
    out["signal_source"] = "QMT Level-2真实订单流"
    out["l2_confirmed"] = False
    out["l2_summary"] = l2

    # Preserve all safety gates from V17: no live/coverage means no prediction.
    if not base.get("live") or base.get("label_60") in ("休市", "数据延迟", "数据补齐中"):
        return out

    lm = l2.get("metrics", {}) or {}
    l2_direction = str(l2.get("direction") or "WATCH")
    l2_agreement = int(l2.get("agreement") or 0)
    r30 = float(out["metrics"].get("change_30s_pct", 0.0) or 0.0)
    r60 = float(out["metrics"].get("change_60s_pct", 0.0) or 0.0)
    active_buy_pct = float(lm.get("active_buy_pct", 50.0) or 50.0)
    big_buy_pct = float(lm.get("big_buy_pct", 50.0) or 50.0)

    # Five independent families: price30, price60, active trade, large money, L2 composite.
    up = 0
    down = 0
    reasons_up = []
    reasons_down = []

    if r30 > 0.02:
        up += 1; reasons_up.append(f"近30秒价格+{r30:.2f}%")
    elif r30 < -0.02:
        down += 1; reasons_down.append(f"近30秒价格{r30:.2f}%")
    if r60 > 0.03:
        up += 1; reasons_up.append(f"近60秒价格+{r60:.2f}%")
    elif r60 < -0.03:
        down += 1; reasons_down.append(f"近60秒价格{r60:.2f}%")

    if active_buy_pct >= 62:
        up += 2; reasons_up.append(f"真实主动买入{active_buy_pct:.0f}%")
    elif active_buy_pct <= 38:
        down += 2; reasons_down.append(f"真实主动卖出{100-active_buy_pct:.0f}%")

    if big_buy_pct >= 62:
        up += 2; reasons_up.append("大/特大单净流入")
    elif big_buy_pct <= 38:
        down += 2; reasons_down.append("大/特大单净流出")

    if l2_direction == "UP" and l2_agreement >= 80:
        up += 2; reasons_up.extend(l2.get("reasons", [])[:2])
    elif l2_direction == "DOWN" and l2_agreement >= 80:
        down += 2; reasons_down.extend(l2.get("reasons", [])[:2])

    total = up + down
    if total <= 0:
        agreement = 0
    else:
        agreement = round(max(up, down) / total * 100)

    out["condition_agreement"] = int(agreement)
    out["signal_strength"] = 0
    out["direction_60"] = "WATCH"
    out["direction_120"] = "WATCH"
    out["label_60"] = "观望"
    out["label_120"] = "观望"
    out["level"] = "灰色"
    out["alert"] = "Level-2资金与价格尚未形成90%同向确认"

    # High-confidence trigger: >=90% family agreement, strong L2, and at least 7 weighted votes.
    if agreement >= 90 and up >= 7 and down == 0 and l2_direction == "UP":
        out["direction_60"] = "UP"
        out["label_60"] = "偏涨｜L2确认"
        out["signal_strength"] = agreement
        out["level"] = "绿色"
        out["alert"] = "真实订单流、主动买盘和短线价格同向偏强；持仓观察，不追高"
        out["reasons"] = list(dict.fromkeys(reasons_up))[:3]
        out["l2_confirmed"] = True
        if r60 > 0 and active_buy_pct >= 58 and big_buy_pct >= 55:
            out["direction_120"] = "UP"
            out["label_120"] = "偏涨｜L2确认"
    elif agreement >= 90 and down >= 7 and up == 0 and l2_direction == "DOWN":
        out["direction_60"] = "DOWN"
        out["label_60"] = "偏跌｜L2确认"
        out["signal_strength"] = agreement
        out["level"] = "红色"
        out["alert"] = "真实订单流、大单资金和短线价格同向转弱；结合既定止损位考虑减仓"
        out["reasons"] = list(dict.fromkeys(reasons_down))[:3]
        out["l2_confirmed"] = True
        if r60 < 0 and active_buy_pct <= 42 and big_buy_pct <= 45:
            out["direction_120"] = "DOWN"
            out["label_120"] = "偏跌｜L2确认"
    elif agreement >= 75:
        out["label_60"] = "接近触发"
        out["alert"] = "Level-2方向正在形成，但还没达到90%门槛"

    out["metrics"].update({
        "l2_active_buy_pct": active_buy_pct,
        "l2_big_buy_pct": big_buy_pct,
        "l2_active_net_amount": float(lm.get("active_net_amount", 0.0) or 0.0),
        "l2_big_net_amount": float(lm.get("big_net_amount", 0.0) or 0.0),
        "l2_cancel_sell_support_pct": float(lm.get("cancel_sell_support_pct", 50.0) or 50.0),
        "l2_total_book_buy_pct": float(lm.get("total_book_buy_pct", 50.0) or 50.0),
        "l2_agreement": l2_agreement,
    })
    return out
