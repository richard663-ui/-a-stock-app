# -*- coding: utf-8 -*-
"""60/120秒方向预警实验引擎。

注意：输出的是高置信度预警，不是保证正确的价格预测。
大部分时间应返回 NEUTRAL，避免强行猜涨跌。
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd


def _safe(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def _weighted_book(row: pd.Series, side: str, levels: int = 5) -> float:
    values = row.get(f"{side}Vol", [])
    if not isinstance(values, (list, tuple)):
        return 0.0
    weights = np.array([1.0 / (i + 1) for i in range(levels)], dtype=float)
    vols = np.array([_safe(x) for x in list(values)[:levels]], dtype=float)
    if len(vols) < levels:
        vols = np.pad(vols, (0, levels - len(vols)))
    return float((vols * weights).sum())


def _slope(series: pd.Series, n: int) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna().tail(n)
    if len(s) < max(3, n // 2):
        return 0.0
    y = s.to_numpy(dtype=float)
    x = np.arange(len(y), dtype=float)
    try:
        return float(np.polyfit(x, y, 1)[0])
    except Exception:
        return 0.0


def _ret_bps(series: pd.Series, n: int) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna().tail(n)
    if len(s) < 2 or s.iloc[0] <= 0:
        return 0.0
    return float((s.iloc[-1] / s.iloc[0] - 1.0) * 10000.0)


def analyze_short_direction(ticks: pd.DataFrame) -> Dict[str, Any]:
    """基于近120秒快照生成 UP/NEUTRAL/DOWN 高置信预警。"""
    base = {
        "ok": False,
        "direction_60": "NEUTRAL",
        "direction_120": "NEUTRAL",
        "label_60": "中性/无信号",
        "label_120": "中性/无信号",
        "confidence": 0,
        "alert": "暂无高置信预警",
        "level": "灰色",
        "reasons": [],
        "score": 0.0,
    }
    if ticks is None or len(ticks) < 30:
        base["reasons"] = ["样本不足：至少需要约30秒实时数据。"]
        return base

    df = ticks.copy().tail(180).reset_index(drop=True)
    for col in ["lastPrice", "volume", "amount", "bidPrice1", "askPrice1"]:
        if col not in df.columns:
            df[col] = np.nan
    price = pd.to_numeric(df["lastPrice"], errors="coerce").ffill()
    if price.dropna().empty:
        base["reasons"] = ["最新价缺失。"]
        return base

    latest = df.iloc[-1]
    bid = _safe(latest.get("bidPrice1"))
    ask = _safe(latest.get("askPrice1"))
    last = _safe(price.iloc[-1])
    spread_bps = ((ask - bid) / last * 10000.0) if last > 0 and ask >= bid > 0 else 0.0

    buy_depth = _weighted_book(latest, "bid", 5)
    sell_depth = _weighted_book(latest, "ask", 5)
    depth_total = buy_depth + sell_depth
    obi = (buy_depth - sell_depth) / depth_total if depth_total > 0 else 0.0

    bid1_vol = _safe((latest.get("bidVol") or [0])[0] if isinstance(latest.get("bidVol"), (list, tuple)) else latest.get("bidVol1"))
    ask1_vol = _safe((latest.get("askVol") or [0])[0] if isinstance(latest.get("askVol"), (list, tuple)) else latest.get("askVol1"))
    microprice = ((ask * bid1_vol + bid * ask1_vol) / (bid1_vol + ask1_vol)) if bid > 0 and ask > 0 and (bid1_vol + ask1_vol) > 0 else last
    micro_edge_bps = (microprice - (bid + ask) / 2.0) / last * 10000.0 if last > 0 and bid > 0 and ask > 0 else 0.0

    r5 = _ret_bps(price, 5)
    r15 = _ret_bps(price, 15)
    r30 = _ret_bps(price, 30)
    r60 = _ret_bps(price, 60)
    slope15 = _slope(price, 15)
    slope30 = _slope(price, 30)

    vol = pd.to_numeric(df["volume"], errors="coerce").ffill().fillna(0)
    amt = pd.to_numeric(df["amount"], errors="coerce").ffill().fillna(0)
    vol_delta_15 = max(0.0, _safe(vol.iloc[-1] - vol.iloc[-min(15, len(vol))]))
    vol_delta_30 = max(0.0, _safe(vol.iloc[-1] - vol.iloc[-min(30, len(vol))]))
    amt_delta_30 = max(0.0, _safe(amt.iloc[-1] - amt.iloc[-min(30, len(amt))]))
    activity_ratio = vol_delta_15 / max(1.0, vol_delta_30 / 2.0) if vol_delta_30 > 0 else 0.0

    score = 0.0
    reasons: List[str] = []

    # 盘口：40分
    if obi >= 0.30:
        score += 20; reasons.append(f"五档买盘占优 OBI={obi:.2f}")
    elif obi <= -0.30:
        score -= 20; reasons.append(f"五档卖盘占优 OBI={obi:.2f}")
    if micro_edge_bps >= 0.8:
        score += 12; reasons.append("微观价格偏向上方")
    elif micro_edge_bps <= -0.8:
        score -= 12; reasons.append("微观价格偏向下方")
    if spread_bps > 8:
        reasons.append("价差偏大，降低置信度")

    # 价格连续性：40分
    momentum_votes = [r5, r15, r30, r60]
    positive = sum(x > 0.8 for x in momentum_votes)
    negative = sum(x < -0.8 for x in momentum_votes)
    if positive >= 3:
        score += 24; reasons.append("5/15/30/60秒价格多数向上")
    elif negative >= 3:
        score -= 24; reasons.append("5/15/30/60秒价格多数向下")
    if slope15 > 0 and slope30 > 0:
        score += 10
    elif slope15 < 0 and slope30 < 0:
        score -= 10

    # 活跃度：只放大已有方向，不创造方向
    activity_boost = 1.0
    if activity_ratio >= 1.5 and amt_delta_30 > 0:
        activity_boost = 1.12
        reasons.append("最近15秒成交活跃度放大")
    score *= activity_boost
    score = float(max(-100.0, min(100.0, score)))

    # 置信度是信号强度，不是已验证胜率
    confidence = int(min(99, max(0, abs(score))))
    if spread_bps > 8:
        confidence = max(0, confidence - 15)

    direction = "NEUTRAL"
    label = "中性/无信号"
    alert = "暂无高置信预警"
    level = "灰色"
    if score >= 72 and confidence >= 80:
        direction, label, alert, level = "UP", "偏涨", "短线转强，继续观察承接", "绿色"
    elif score <= -72 and confidence >= 80:
        direction, label, alert, level = "DOWN", "偏跌", "短线转弱，持仓注意减仓条件", "橙色"

    # 只有极端一致时进入90阈值；大部分时间保持中性
    if confidence >= 90 and direction == "DOWN":
        alert, level = "高置信转弱：检查预设止损/减仓条件", "红色"
    elif confidence >= 90 and direction == "UP":
        alert, level = "高置信转强：不追高，等回踩确认", "深绿色"

    return {
        "ok": True,
        "direction_60": direction,
        "direction_120": direction if abs(r60) >= 0.8 else "NEUTRAL",
        "label_60": label,
        "label_120": label if abs(r60) >= 0.8 else "中性/无信号",
        "confidence": confidence,
        "alert": alert,
        "level": level,
        "reasons": reasons[:4] or ["盘口与价格方向不一致。"],
        "score": score,
        "metrics": {
            "obi": obi,
            "micro_edge_bps": micro_edge_bps,
            "spread_bps": spread_bps,
            "r15_bps": r15,
            "r30_bps": r30,
            "r60_bps": r60,
            "activity_ratio": activity_ratio,
        },
    }
