# -*- coding: utf-8 -*-
"""High-confidence 60/120-second direction engine for QMT snapshots.

The engine is time-based rather than row-count based. It uses price momentum,
estimated aggressive trade flow, five-level order-book pressure, short VWAP,
microprice and signal persistence. It only emits UP/DOWN when the directional
conditions are highly consistent; otherwise it returns WATCH.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

CN_TZ = ZoneInfo("Asia/Shanghai")


def _safe(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if np.isfinite(number) else default
    except Exception:
        return default


def _as_list(value: Any) -> List[float]:
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_safe(x) for x in list(value)]
    return []


def _parse_times(df: pd.DataFrame) -> pd.Series:
    raw = df.get("captured_at", pd.Series(index=df.index, dtype=object))
    parsed = pd.to_datetime(raw, errors="coerce")
    if parsed.notna().any():
        try:
            if parsed.dt.tz is None:
                parsed = parsed.dt.tz_localize(CN_TZ)
            else:
                parsed = parsed.dt.tz_convert(CN_TZ)
            return parsed
        except Exception:
            pass

    timetag = df.get("timetag", pd.Series(index=df.index, dtype=object))
    parsed = pd.to_datetime(timetag, errors="coerce")
    try:
        if parsed.dt.tz is None:
            parsed = parsed.dt.tz_localize(CN_TZ)
        else:
            parsed = parsed.dt.tz_convert(CN_TZ)
    except Exception:
        pass
    return parsed


def _prepare_ticks(ticks: pd.DataFrame) -> pd.DataFrame:
    if ticks is None or ticks.empty:
        return pd.DataFrame()

    df = ticks.copy()
    df["_ts"] = _parse_times(df)
    df["lastPrice"] = pd.to_numeric(df.get("lastPrice"), errors="coerce")
    df["volume"] = pd.to_numeric(df.get("volume"), errors="coerce")
    df["amount"] = pd.to_numeric(df.get("amount"), errors="coerce")
    df = df.dropna(subset=["lastPrice"])
    df = df[df["lastPrice"] > 0]
    if df.empty:
        return df

    if df["_ts"].notna().any():
        df = df.sort_values("_ts")
        df = df.drop_duplicates(subset=["_ts", "lastPrice", "volume"], keep="last")
    else:
        df = df.reset_index(drop=True)
    return df.tail(1200).reset_index(drop=True)


def _window(df: pd.DataFrame, seconds: int) -> pd.DataFrame:
    if df.empty:
        return df
    if df["_ts"].notna().any():
        end = df["_ts"].dropna().iloc[-1]
        start = end - pd.Timedelta(seconds=seconds)
        part = df[df["_ts"] >= start]
        if len(part) >= 2:
            return part
    return df.tail(max(2, min(len(df), seconds)))


def _return_pct(df: pd.DataFrame, seconds: int) -> float:
    part = _window(df, seconds)
    if len(part) < 2:
        return 0.0
    first = _safe(part["lastPrice"].iloc[0])
    last = _safe(part["lastPrice"].iloc[-1])
    return (last / first - 1.0) * 100.0 if first > 0 else 0.0


def _book_depth(row: pd.Series, levels: int = 5) -> Tuple[float, float]:
    bid = _as_list(row.get("bidVol"))[:levels]
    ask = _as_list(row.get("askVol"))[:levels]
    weights = np.array([1.0, 0.78, 0.58, 0.40, 0.25][:levels], dtype=float)
    b = np.pad(np.array(bid, dtype=float), (0, max(0, levels - len(bid))))[:levels]
    a = np.pad(np.array(ask, dtype=float), (0, max(0, levels - len(ask))))[:levels]
    return float((b * weights).sum()), float((a * weights).sum())


def _buy_pressure(row: pd.Series) -> float:
    buy, sell = _book_depth(row)
    total = buy + sell
    return buy / total * 100.0 if total > 0 else 50.0


def _best_prices(row: pd.Series) -> Tuple[float, float, float, float]:
    bids = _as_list(row.get("bidPrice"))
    asks = _as_list(row.get("askPrice"))
    bidv = _as_list(row.get("bidVol"))
    askv = _as_list(row.get("askVol"))
    bid1 = bids[0] if bids else _safe(row.get("bidPrice1"))
    ask1 = asks[0] if asks else _safe(row.get("askPrice1"))
    bv1 = bidv[0] if bidv else _safe(row.get("bidVol1"))
    av1 = askv[0] if askv else _safe(row.get("askVol1"))
    return bid1, ask1, bv1, av1


def _recent_vwap(df: pd.DataFrame, seconds: int = 60) -> float:
    part = _window(df, seconds)
    if len(part) < 2:
        return 0.0
    volume = part["volume"].ffill()
    amount = part["amount"].ffill()
    if volume.dropna().empty or amount.dropna().empty:
        return 0.0
    delta_volume_lots = _safe(volume.iloc[-1] - volume.iloc[0])
    delta_amount = _safe(amount.iloc[-1] - amount.iloc[0])
    return delta_amount / (delta_volume_lots * 100.0) if delta_volume_lots > 0 and delta_amount > 0 else 0.0


def _trade_flow(df: pd.DataFrame, seconds: int = 60) -> Dict[str, float]:
    part = _window(df, seconds).copy()
    default = {
        "buy_lots": 0.0,
        "sell_lots": 0.0,
        "neutral_lots": 0.0,
        "buy_pct": 50.0,
        "net_lots": 0.0,
        "thousand_buy": 0,
        "thousand_sell": 0,
        "ten_thousand_buy": 0,
        "ten_thousand_sell": 0,
        "events": 0,
    }
    if len(part) < 3:
        return default

    part["dvol"] = part["volume"].diff().clip(lower=0)
    prices = part["lastPrice"].astype(float)
    prev_prices = prices.shift(1)
    signs: List[int] = [0] * len(part)

    for i in range(1, len(part)):
        price = _safe(prices.iloc[i])
        prev_price = _safe(prev_prices.iloc[i])
        prev = part.iloc[i - 1]
        bid1, ask1, _, _ = _best_prices(prev)
        if ask1 > 0 and price >= ask1:
            signs[i] = 1
        elif bid1 > 0 and price <= bid1:
            signs[i] = -1
        elif price > prev_price:
            signs[i] = 1
        elif price < prev_price:
            signs[i] = -1
        else:
            signs[i] = signs[i - 1] if i > 1 else 0

    part["sign"] = signs
    buy = float(part.loc[part["sign"] > 0, "dvol"].sum())
    sell = float(part.loc[part["sign"] < 0, "dvol"].sum())
    neutral = float(part.loc[part["sign"] == 0, "dvol"].sum())
    directional = buy + sell
    result = dict(default)
    result.update(
        buy_lots=buy,
        sell_lots=sell,
        neutral_lots=neutral,
        buy_pct=(buy / directional * 100.0) if directional > 0 else 50.0,
        net_lots=buy - sell,
        thousand_buy=int(((part["sign"] > 0) & (part["dvol"] >= 1000)).sum()),
        thousand_sell=int(((part["sign"] < 0) & (part["dvol"] >= 1000)).sum()),
        ten_thousand_buy=int(((part["sign"] > 0) & (part["dvol"] >= 10000)).sum()),
        ten_thousand_sell=int(((part["sign"] < 0) & (part["dvol"] >= 10000)).sum()),
        events=int((part["dvol"] > 0).sum()),
    )
    return result


def _market_open(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return (570 <= minute <= 690) or (780 <= minute <= 900)


def analyze_short_direction(ticks: pd.DataFrame) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "ok": False,
        "live": False,
        "direction_60": "WATCH",
        "direction_120": "WATCH",
        "label_60": "等待实时数据",
        "label_120": "等待实时数据",
        "signal_strength": 0,
        "condition_agreement": 0,
        "alert": "实时数据正在补齐",
        "level": "灰色",
        "reasons": [],
        "metrics": {},
    }

    df = _prepare_ticks(ticks)
    if len(df) < 3:
        base["reasons"] = ["尚未收到足够的QMT行情"]
        return base

    latest = df.iloc[-1]
    last = _safe(latest.get("lastPrice"))
    latest_ts = df["_ts"].dropna().iloc[-1] if df["_ts"].notna().any() else None
    now = datetime.now(CN_TZ)
    age_seconds = None
    if latest_ts is not None:
        try:
            age_seconds = max(0.0, (pd.Timestamp(now) - latest_ts).total_seconds())
        except Exception:
            age_seconds = None

    market_open = _market_open(now)
    live = bool(age_seconds is not None and age_seconds <= 15 and market_open)
    coverage_seconds = 0.0
    if df["_ts"].notna().sum() >= 2:
        coverage_seconds = max(
            0.0,
            (df["_ts"].dropna().iloc[-1] - df["_ts"].dropna().iloc[0]).total_seconds(),
        )

    r10 = _return_pct(df, 10)
    r30 = _return_pct(df, 30)
    r60 = _return_pct(df, 60)
    r120 = _return_pct(df, 120)

    recent_book = _window(df, 12)
    pressures = recent_book.apply(_buy_pressure, axis=1) if not recent_book.empty else pd.Series(dtype=float)
    buy_pressure = float(pressures.mean()) if not pressures.empty else 50.0
    prior_book = _window(df, 35)
    if len(prior_book) > len(recent_book):
        prior_part = prior_book.iloc[: max(1, len(prior_book) - len(recent_book))]
        prior_pressure = float(prior_part.apply(_buy_pressure, axis=1).mean())
    else:
        prior_pressure = buy_pressure
    pressure_change = buy_pressure - prior_pressure

    bid1, ask1, bv1, av1 = _best_prices(latest)
    spread_pct = ((ask1 - bid1) / last * 100.0) if last > 0 and ask1 >= bid1 > 0 else 0.0
    noise = max(0.02, spread_pct * 1.5)

    microprice = 0.0
    if bid1 > 0 and ask1 > 0 and bv1 + av1 > 0:
        microprice = (ask1 * bv1 + bid1 * av1) / (bv1 + av1)
    micro_bias_pct = ((microprice / last - 1.0) * 100.0) if last > 0 and microprice > 0 else 0.0

    recent_vwap = _recent_vwap(df, 60)
    above_vwap_pct = ((last / recent_vwap - 1.0) * 100.0) if recent_vwap > 0 else 0.0
    flow = _trade_flow(df, 60)

    high = _safe(latest.get("high"))
    low = _safe(latest.get("low"))
    day_position = (last - low) / (high - low) * 100.0 if high > low > 0 else 50.0

    score = 0.0
    up_reasons: List[str] = []
    down_reasons: List[str] = []
    up_votes = 0
    down_votes = 0

    if r10 >= noise:
        score += 8; up_votes += 1
    elif r10 <= -noise:
        score -= 8; down_votes += 1

    if r30 >= noise * 1.5:
        score += 15; up_votes += 1; up_reasons.append(f"近30秒上涨{r30:.2f}%")
    elif r30 <= -noise * 1.5:
        score -= 15; down_votes += 1; down_reasons.append(f"近30秒下跌{abs(r30):.2f}%")

    if r60 >= noise * 2.0:
        score += 17; up_votes += 1; up_reasons.append(f"近60秒上涨{r60:.2f}%")
    elif r60 <= -noise * 2.0:
        score -= 17; down_votes += 1; down_reasons.append(f"近60秒下跌{abs(r60):.2f}%")

    if flow["events"] >= 3 and flow["buy_pct"] >= 63:
        score += 25; up_votes += 1; up_reasons.append(f"主动买入估算占{flow['buy_pct']:.0f}%")
    elif flow["events"] >= 3 and flow["buy_pct"] <= 37:
        score -= 25; down_votes += 1; down_reasons.append(f"主动卖出估算占{100-flow['buy_pct']:.0f}%")

    if buy_pressure >= 60:
        score += 14; up_votes += 1; up_reasons.append(f"五档买盘承接占{buy_pressure:.0f}%")
    elif buy_pressure <= 40:
        score -= 14; down_votes += 1; down_reasons.append(f"五档卖盘压力占{100-buy_pressure:.0f}%")

    if recent_vwap > 0 and above_vwap_pct >= noise:
        score += 13; up_votes += 1; up_reasons.append("价格站在近60秒成交均价上方")
    elif recent_vwap > 0 and above_vwap_pct <= -noise:
        score -= 13; down_votes += 1; down_reasons.append("价格跌到近60秒成交均价下方")

    if micro_bias_pct >= noise * 0.35 or pressure_change >= 6:
        score += 8; up_votes += 1
    elif micro_bias_pct <= -noise * 0.35 or pressure_change <= -6:
        score -= 8; down_votes += 1

    score = float(max(-100.0, min(100.0, score)))
    agreement = int(round(abs(score)))
    direction_60 = "WATCH"
    direction_120 = "WATCH"
    label_60 = "观望"
    label_120 = "观望"
    level = "灰色"
    alert = "未达到90%条件一致度，继续观察"
    reasons: List[str] = [
        f"近30秒{r30:+.2f}%",
        f"主动买入估算{flow['buy_pct']:.0f}%",
        f"五档买盘{buy_pressure:.0f}%",
    ]

    ready = coverage_seconds >= 55 and len(df) >= 20
    if not market_open:
        label_60 = label_120 = "休市"
        alert = "当前休市，短线预测暂停；日线与基本面仍可查看"
    elif age_seconds is None or age_seconds > 15:
        label_60 = label_120 = "数据延迟"
        alert = "QMT数据超过15秒未更新，暂停短线预测"
    elif not ready:
        label_60 = label_120 = "数据补齐中"
        alert = f"正在积累实时样本，已覆盖约{coverage_seconds:.0f}秒"
    elif score >= 90 and up_votes >= 5 and down_votes == 0:
        direction_60 = "UP"
        label_60 = "偏涨｜高置信"
        level = "绿色"
        alert = "短线多项条件同向偏强；持仓观察，不追高"
        reasons = up_reasons[:3]
        if r120 >= noise * 2.0 and flow["buy_pct"] >= 58 and buy_pressure >= 55:
            direction_120 = "UP"
            label_120 = "偏涨｜高置信"
    elif score <= -90 and down_votes >= 5 and up_votes == 0:
        direction_60 = "DOWN"
        label_60 = "偏跌｜高置信"
        level = "红色"
        alert = "短线多项条件同向转弱；结合既定止损位考虑减仓"
        reasons = down_reasons[:3]
        if r120 <= -noise * 2.0 and flow["buy_pct"] <= 42 and buy_pressure <= 45:
            direction_120 = "DOWN"
            label_120 = "偏跌｜高置信"
    elif agreement >= 75:
        label_60 = "接近触发"
        alert = "方向正在形成，但尚未达到90%高置信门槛"

    return {
        "ok": True,
        "live": live,
        "direction_60": direction_60,
        "direction_120": direction_120,
        "label_60": label_60,
        "label_120": label_120,
        "signal_strength": agreement if direction_60 != "WATCH" else 0,
        "condition_agreement": agreement,
        "alert": alert,
        "level": level,
        "reasons": reasons[:3],
        "score": score,
        "metrics": {
            "buy_pressure_pct": buy_pressure,
            "sell_pressure_pct": 100.0 - buy_pressure,
            "pressure_change_pct": pressure_change,
            "change_10s_pct": r10,
            "change_30s_pct": r30,
            "change_60s_pct": r60,
            "change_120s_pct": r120,
            "recent_vwap": recent_vwap,
            "above_vwap_pct": above_vwap_pct,
            "microprice_bias_pct": micro_bias_pct,
            "day_position_pct": day_position,
            "spread_pct": spread_pct,
            "coverage_seconds": coverage_seconds,
            "age_seconds": age_seconds,
            "market_open": market_open,
            **flow,
        },
    }
