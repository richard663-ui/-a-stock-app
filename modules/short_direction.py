# -*- coding: utf-8 -*-
"""未来 60/120 秒方向实验引擎。

只在价格、盘口承接和近期成交均价方向一致时给出偏涨/偏跌；
其余时间返回“暂无明确方向”。信号强度不是已验证胜率。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


def _safe(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def _ret_pct(series: pd.Series, n: int) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna().tail(n)
    if len(s) < 2 or s.iloc[0] <= 0:
        return 0.0
    return float((s.iloc[-1] / s.iloc[0] - 1.0) * 100.0)


def _book_depth(row: pd.Series, levels: int = 5) -> Tuple[float, float]:
    bid = row.get("bidVol", [])
    ask = row.get("askVol", [])
    if not isinstance(bid, (list, tuple)):
        bid = []
    if not isinstance(ask, (list, tuple)):
        ask = []
    weights = np.array([1.0, 0.8, 0.6, 0.4, 0.2][:levels], dtype=float)
    b = np.array([_safe(x) for x in list(bid)[:levels]], dtype=float)
    a = np.array([_safe(x) for x in list(ask)[:levels]], dtype=float)
    if len(b) < levels:
        b = np.pad(b, (0, levels - len(b)))
    if len(a) < levels:
        a = np.pad(a, (0, levels - len(a)))
    return float((b * weights).sum()), float((a * weights).sum())


def _buy_pressure(row: pd.Series) -> float:
    buy, sell = _book_depth(row)
    total = buy + sell
    return buy / total * 100.0 if total > 0 else 50.0


def _recent_vwap(df: pd.DataFrame, n: int = 60) -> float:
    part = df.tail(n).copy()
    if len(part) < 2:
        return 0.0
    volume = pd.to_numeric(part.get("volume"), errors="coerce").ffill()
    amount = pd.to_numeric(part.get("amount"), errors="coerce").ffill()
    if volume.dropna().empty or amount.dropna().empty:
        return 0.0
    dv = _safe(volume.iloc[-1] - volume.iloc[0])
    da = _safe(amount.iloc[-1] - amount.iloc[0])
    return da / dv if dv > 0 and da > 0 else 0.0


def analyze_short_direction(ticks: pd.DataFrame) -> Dict[str, Any]:
    base = {
        "ok": False,
        "direction_60": "NEUTRAL",
        "direction_120": "NEUTRAL",
        "label_60": "暂无明确方向",
        "label_120": "暂无明确方向",
        "signal_strength": 0,
        "confidence": 0,
        "alert": "实时样本不足，暂不判断",
        "level": "灰色",
        "reasons": [],
        "metrics": {},
    }
    if ticks is None or len(ticks) < 45:
        base["reasons"] = ["至少需要约45秒实时数据"]
        return base

    df = ticks.tail(240).copy().reset_index(drop=True)
    price = pd.to_numeric(df.get("lastPrice"), errors="coerce").ffill()
    if price.dropna().empty:
        base["reasons"] = ["最新价缺失"]
        return base

    latest = df.iloc[-1]
    last = _safe(price.iloc[-1])
    bid_prices = latest.get("bidPrice", [])
    ask_prices = latest.get("askPrice", [])
    bid1 = _safe(bid_prices[0] if isinstance(bid_prices, (list, tuple)) and bid_prices else latest.get("bidPrice1"))
    ask1 = _safe(ask_prices[0] if isinstance(ask_prices, (list, tuple)) and ask_prices else latest.get("askPrice1"))
    spread_pct = ((ask1 - bid1) / last * 100.0) if last > 0 and ask1 >= bid1 > 0 else 0.0
    noise = max(0.025, spread_pct * 1.2)

    r10 = _ret_pct(price, 10)
    r30 = _ret_pct(price, 30)
    r60 = _ret_pct(price, 60)
    r120 = _ret_pct(price, 120)

    recent = df.tail(10)
    pressures = recent.apply(_buy_pressure, axis=1)
    buy_pressure = float(pressures.mean()) if not pressures.empty else 50.0
    recent_vwap = _recent_vwap(df, 60)
    above_vwap_pct = ((last / recent_vwap - 1.0) * 100.0) if recent_vwap > 0 else 0.0

    high = _safe(latest.get("high"))
    low = _safe(latest.get("low"))
    day_position = (last - low) / (high - low) * 100.0 if high > low > 0 else 50.0

    volume = pd.to_numeric(df.get("volume"), errors="coerce").ffill().fillna(0)
    v15 = max(0.0, _safe(volume.iloc[-1] - volume.iloc[-15])) if len(volume) >= 15 else 0.0
    v60 = max(0.0, _safe(volume.iloc[-1] - volume.iloc[-60])) if len(volume) >= 60 else 0.0
    activity = v15 / max(1.0, v60 / 4.0) if v60 > 0 else 1.0

    score = 0.0
    up_votes = 0
    down_votes = 0
    reasons_up: List[str] = []
    reasons_down: List[str] = []

    if r10 > noise:
        score += 10; up_votes += 1
    elif r10 < -noise:
        score -= 10; down_votes += 1
    if r30 > noise * 1.5:
        score += 20; up_votes += 1; reasons_up.append(f"近30秒上涨{r30:.2f}%")
    elif r30 < -noise * 1.5:
        score -= 20; down_votes += 1; reasons_down.append(f"近30秒下跌{abs(r30):.2f}%")
    if r60 > noise * 2.0:
        score += 22; up_votes += 1; reasons_up.append(f"近60秒上涨{r60:.2f}%")
    elif r60 < -noise * 2.0:
        score -= 22; down_votes += 1; reasons_down.append(f"近60秒下跌{abs(r60):.2f}%")

    if buy_pressure >= 58:
        score += 22; up_votes += 1; reasons_up.append(f"五档买盘承接占{buy_pressure:.0f}%")
    elif buy_pressure <= 42:
        score -= 22; down_votes += 1; reasons_down.append(f"五档卖盘压力占{100-buy_pressure:.0f}%")

    if recent_vwap > 0 and above_vwap_pct >= noise:
        score += 18; up_votes += 1; reasons_up.append("价格站在近60秒成交均价上方")
    elif recent_vwap > 0 and above_vwap_pct <= -noise:
        score -= 18; down_votes += 1; reasons_down.append("价格跌到近60秒成交均价下方")

    if day_position >= 70:
        score += 5
    elif day_position <= 30:
        score -= 5
    if activity >= 1.35:
        score *= 1.08

    score = float(max(-100.0, min(100.0, score)))
    strength = int(min(99, abs(score)))

    direction_60 = "NEUTRAL"
    label_60 = "暂无明确方向"
    direction_120 = "NEUTRAL"
    label_120 = "暂无明确方向"
    alert = "盘口与价格没有形成一致方向"
    level = "灰色"
    reasons: List[str] = []

    if score >= 72 and up_votes >= 3:
        direction_60, label_60 = "UP", "偏涨"
        alert, level = "短线偏强：持仓可继续观察，不要追高", "绿色"
        reasons = reasons_up
        if r120 > noise * 2 and buy_pressure >= 55:
            direction_120, label_120 = "UP", "偏涨"
    elif score <= -72 and down_votes >= 3:
        direction_60, label_60 = "DOWN", "偏跌"
        alert, level = "短线转弱：跌破风控位时再考虑减仓", "橙色"
        reasons = reasons_down
        if r120 < -noise * 2 and buy_pressure <= 45:
            direction_120, label_120 = "DOWN", "偏跌"
    else:
        strength = 0
        reasons = [
            f"近30秒涨跌{r30:+.2f}%",
            f"五档买盘占{buy_pressure:.0f}%",
            "价格、盘口与成交均价尚未同向",
        ]

    return {
        "ok": True,
        "direction_60": direction_60,
        "direction_120": direction_120,
        "label_60": label_60,
        "label_120": label_120,
        "signal_strength": strength,
        "confidence": strength,
        "alert": alert,
        "level": level,
        "reasons": reasons[:3],
        "score": score,
        "metrics": {
            "buy_pressure_pct": buy_pressure,
            "sell_pressure_pct": 100.0 - buy_pressure,
            "change_10s_pct": r10,
            "change_30s_pct": r30,
            "change_60s_pct": r60,
            "change_120s_pct": r120,
            "recent_vwap": recent_vwap,
            "above_vwap_pct": above_vwap_pct,
            "day_position_pct": day_position,
            "activity_ratio": activity,
            "spread_pct": spread_pct,
        },
    }
