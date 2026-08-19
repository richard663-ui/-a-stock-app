# -*- coding: utf-8 -*-
"""60/120-second direction engine for the V17 QMT dashboard.

Design goals:
1. The 1-minute direction is always present once enough live data exists.
2. "90%" on screen means condition agreement, never a fake historical win rate.
3. A HIGH-confidence alert requires broad evidence agreement and, when possible,
   true QMT Level-2 transaction/order/cancel data.
4. The engine remains usable with ordinary QMT snapshots, but labels that path
   as fallback rather than pretending it is true order flow.
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


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return float(max(lo, min(hi, value)))


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
    return df.tail(1800).reset_index(drop=True)


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
    base_weights = [1.0, 0.82, 0.68, 0.55, 0.45, 0.36, 0.29, 0.23, 0.18, 0.14]
    weights = np.array(base_weights[:levels], dtype=float)
    b = np.pad(np.array(bid, dtype=float), (0, max(0, levels - len(bid))))[:levels]
    a = np.pad(np.array(ask, dtype=float), (0, max(0, levels - len(ask))))[:levels]
    return float((b * weights).sum()), float((a * weights).sum())


def _buy_pressure(row: pd.Series, levels: int = 5) -> float:
    buy, sell = _book_depth(row, levels)
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


def _fallback_trade_flow(df: pd.DataFrame, seconds: int = 60) -> Dict[str, float]:
    """Snapshot-based Lee/Ready-like fallback. Not true Level-2."""
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
        "flow_source": "snapshot_estimate",
    }
    if len(part) < 3:
        return default

    part["dvol"] = part["volume"].diff().clip(lower=0)
    prices = part["lastPrice"].astype(float)
    signs: List[int] = [0] * len(part)
    for i in range(1, len(part)):
        price = _safe(prices.iloc[i])
        prev_price = _safe(prices.iloc[i - 1])
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


def _feature(side_value: float, weight: float, name: str, reason_up: str, reason_down: str) -> Dict[str, Any]:
    value = _clip(side_value)
    return {
        "name": name,
        "weight": float(weight),
        "value": value,
        "score": value * float(weight),
        "reason_up": reason_up,
        "reason_down": reason_down,
    }


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
        "evidence_coverage": 0,
        "high_confidence": False,
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
    pressures5 = recent_book.apply(lambda row: _buy_pressure(row, 5), axis=1) if not recent_book.empty else pd.Series(dtype=float)
    pressures10 = recent_book.apply(lambda row: _buy_pressure(row, 10), axis=1) if not recent_book.empty else pd.Series(dtype=float)
    buy_pressure = float(pressures5.mean()) if not pressures5.empty else 50.0
    buy_pressure10 = float(pressures10.mean()) if not pressures10.empty else buy_pressure

    prior_book = _window(df, 35)
    if len(prior_book) > len(recent_book):
        prior_part = prior_book.iloc[: max(1, len(prior_book) - len(recent_book))]
        prior_pressure = float(prior_part.apply(lambda row: _buy_pressure(row, 5), axis=1).mean())
    else:
        prior_pressure = buy_pressure
    pressure_change = buy_pressure - prior_pressure

    bid1, ask1, bv1, av1 = _best_prices(latest)
    spread_pct = ((ask1 - bid1) / last * 100.0) if last > 0 and ask1 >= bid1 > 0 else 0.0
    noise = max(0.015, spread_pct * 1.25)

    microprice = 0.0
    if bid1 > 0 and ask1 > 0 and bv1 + av1 > 0:
        microprice = (ask1 * bv1 + bid1 * av1) / (bv1 + av1)
    micro_bias_pct = ((microprice / last - 1.0) * 100.0) if last > 0 and microprice > 0 else 0.0

    recent_vwap = _recent_vwap(df, 60)
    above_vwap_pct = ((last / recent_vwap - 1.0) * 100.0) if recent_vwap > 0 else 0.0
    fallback_flow = _fallback_trade_flow(df, 60)

    l2_true = _bool(latest.get("l2_true"))
    l2_periods = latest.get("l2_periods") if isinstance(latest.get("l2_periods"), list) else []
    l2_tx_events = int(_safe(latest.get("l2_trade_events_60s")))
    l2_tx_buy_pct = _safe(latest.get("l2_trade_buy_pct_60s"), 50.0)
    l2_order_events = int(_safe(latest.get("l2_order_events_60s")))
    l2_order_buy_pct = _safe(latest.get("l2_order_buy_pct_60s"), 50.0)
    l2_sell_cancel_pct = _safe(latest.get("l2_sell_cancel_pct_60s"), 50.0)
    l2_total_bid_pct = _safe(latest.get("l2_total_bid_pct"), 50.0)
    l2_queue_buy_pct = _safe(latest.get("l2_queue_buy_pct"), 50.0)

    if l2_true and l2_tx_events > 0:
        flow = {
            "buy_lots": _safe(latest.get("l2_trade_buy_lots_60s")),
            "sell_lots": _safe(latest.get("l2_trade_sell_lots_60s")),
            "neutral_lots": 0.0,
            "buy_pct": l2_tx_buy_pct,
            "net_lots": _safe(latest.get("l2_net_active_lots_60s")),
            "thousand_buy": int(_safe(latest.get("l2_thousand_buy"))),
            "thousand_sell": int(_safe(latest.get("l2_thousand_sell"))),
            "ten_thousand_buy": int(_safe(latest.get("l2_ten_thousand_buy"))),
            "ten_thousand_sell": int(_safe(latest.get("l2_ten_thousand_sell"))),
            "events": l2_tx_events,
            "flow_source": "true_l2transaction",
        }
    else:
        flow = fallback_flow

    features: List[Dict[str, Any]] = []

    momentum_mix = 0.18 * r10 + 0.34 * r30 + 0.48 * r60
    features.append(_feature(
        momentum_mix / max(noise * 2.6, 0.04), 20, "momentum",
        f"近60秒价格结构偏强（{r60:+.2f}%）",
        f"近60秒价格结构偏弱（{r60:+.2f}%）",
    ))

    # True transaction flow gets the largest weight because this is the
    # closest observable proxy for who is actually crossing the spread.
    if l2_true and l2_tx_events >= 3:
        features.append(_feature(
            (l2_tx_buy_pct - 50.0) / 16.0, 25, "l2_transaction",
            f"逐笔主动买入占{l2_tx_buy_pct:.0f}%",
            f"逐笔主动卖出占{100-l2_tx_buy_pct:.0f}%",
        ))
    elif fallback_flow["events"] >= 3:
        features.append(_feature(
            (fallback_flow["buy_pct"] - 50.0) / 20.0, 12, "estimated_trade_flow",
            f"成交方向估算偏买（{fallback_flow['buy_pct']:.0f}%）",
            f"成交方向估算偏卖（{100-fallback_flow['buy_pct']:.0f}%）",
        ))

    if l2_true and l2_order_events >= 3:
        features.append(_feature(
            (l2_order_buy_pct - 50.0) / 18.0, 15, "l2_order",
            f"逐笔新增买单占优（{l2_order_buy_pct:.0f}%）",
            f"逐笔新增卖单占优（{100-l2_order_buy_pct:.0f}%）",
        ))

    if l2_true and "l2quoteaux" in l2_periods:
        features.append(_feature(
            (l2_sell_cancel_pct - 50.0) / 20.0, 10, "l2_cancel",
            f"卖单撤单更明显（{l2_sell_cancel_pct:.0f}%）",
            f"买单撤单更明显（{100-l2_sell_cancel_pct:.0f}%）",
        ))

    features.append(_feature(
        ((buy_pressure * 0.65 + buy_pressure10 * 0.35) - 50.0) / 18.0, 10, "book_depth",
        f"盘口买方承接较强（{buy_pressure:.0f}%）",
        f"盘口卖方压力较强（{100-buy_pressure:.0f}%）",
    ))

    if l2_true and "l2quoteaux" in l2_periods:
        features.append(_feature(
            (l2_total_bid_pct - 50.0) / 20.0, 5, "total_book",
            f"总委买量占优（{l2_total_bid_pct:.0f}%）",
            f"总委卖量占优（{100-l2_total_bid_pct:.0f}%）",
        ))

    if l2_true and "l2orderqueue" in l2_periods:
        features.append(_feature(
            (l2_queue_buy_pct - 50.0) / 20.0, 5, "best_queue",
            f"买一队列承接偏强（{l2_queue_buy_pct:.0f}%）",
            f"卖一队列压单偏强（{100-l2_queue_buy_pct:.0f}%）",
        ))

    if recent_vwap > 0:
        features.append(_feature(
            above_vwap_pct / max(noise * 2.2, 0.035), 7, "short_vwap",
            "价格位于近60秒成交均价上方",
            "价格位于近60秒成交均价下方",
        ))

    micro_mix = micro_bias_pct + (pressure_change / 100.0) * max(noise, 0.02)
    features.append(_feature(
        micro_mix / max(noise * 0.75, 0.012), 3, "microprice",
        "最优盘口价格重心偏上",
        "最优盘口价格重心偏下",
    ))

    available_weight = float(sum(f["weight"] for f in features))
    raw_score = float(sum(f["score"] for f in features))
    score = 0.0 if available_weight <= 0 else raw_score / available_weight * 100.0
    score = float(max(-100.0, min(100.0, score)))

    positive_weight = sum(f["weight"] for f in features if f["value"] >= 0.15)
    negative_weight = sum(f["weight"] for f in features if f["value"] <= -0.15)
    directional_weight = positive_weight + negative_weight
    dominant_weight = max(positive_weight, negative_weight)
    agreement = int(round(dominant_weight / directional_weight * 100.0)) if directional_weight > 0 else 50
    evidence_coverage = int(round(directional_weight / available_weight * 100.0)) if available_weight > 0 else 0
    signal_strength = int(round(abs(score)))

    ready = coverage_seconds >= 55 and len(df) >= 20
    high_confidence = bool(
        ready
        and live
        and l2_true
        and agreement >= 90
        and evidence_coverage >= 60
        and signal_strength >= 45
        and directional_weight >= 50
    )

    direction_60 = "WATCH"
    direction_120 = "WATCH"
    label_60 = "观望"
    label_120 = "观望"
    level = "灰色"
    alert = "实时方向正在形成"

    if not market_open:
        label_60 = label_120 = "休市"
        alert = "当前休市，1分钟方向暂停；波段趋势仍可查看"
    elif age_seconds is None or age_seconds > 15:
        label_60 = label_120 = "数据延迟"
        alert = "QMT数据超过15秒未更新，暂停1分钟方向"
    elif not ready:
        label_60 = label_120 = "数据补齐中"
        alert = f"正在积累实时样本，已覆盖约{coverage_seconds:.0f}秒"
    else:
        # Father's requirement: keep a visible 1-minute directional lean.
        direction_60 = "UP" if score >= 0 else "DOWN"
        arrow = "↑" if direction_60 == "UP" else "↓"
        side = "偏涨" if direction_60 == "UP" else "偏跌"
        if high_confidence:
            label_60 = f"{arrow} {side}｜高置信"
            level = "绿色" if direction_60 == "UP" else "红色"
            if direction_60 == "UP":
                alert = "未来1分钟买方明显占优；作为波段入场/持仓确认，不建议单独追涨"
            else:
                alert = "未来1分钟卖方明显占优；若波段结构同时转弱，考虑减仓或收紧止损"
        elif signal_strength >= 45:
            label_60 = f"{arrow} {side}｜中等"
            alert = "1分钟方向已给出，但未达到90%条件一致门槛"
        else:
            label_60 = f"{arrow} {side}｜弱"
            alert = "方向很弱，只保留提示，不作为单独买卖依据"

        # Two-minute layer is deliberately slower and more conservative.
        two_min_score = 0.62 * score + 0.38 * _clip(r120 / max(noise * 4.0, 0.08)) * 100.0
        direction_120 = "UP" if two_min_score >= 0 else "DOWN"
        arrow2 = "↑" if direction_120 == "UP" else "↓"
        side2 = "偏涨" if direction_120 == "UP" else "偏跌"
        label_120 = f"{arrow2} {side2}"
        if high_confidence and direction_120 == direction_60:
            label_120 += "｜同向确认"

    dominant_up = direction_60 == "UP"
    ordered = sorted(features, key=lambda f: abs(f["score"]), reverse=True)
    reasons: List[str] = []
    for feature in ordered:
        if dominant_up and feature["value"] >= 0.15:
            reasons.append(feature["reason_up"])
        elif not dominant_up and feature["value"] <= -0.15:
            reasons.append(feature["reason_down"])
        if len(reasons) >= 3:
            break
    if not reasons:
        reasons = [
            f"近30秒{r30:+.2f}%",
            f"近60秒{r60:+.2f}%",
            f"五档买盘{buy_pressure:.0f}%",
        ]

    high_acc = latest.get("validated_l2_high_accuracy_60")
    high_samples = int(_safe(latest.get("validated_l2_high_samples_60")))
    all_acc = latest.get("validated_all_accuracy_60")
    all_samples = int(_safe(latest.get("validated_all_samples_60")))

    return {
        "ok": True,
        "live": live,
        "direction_60": direction_60,
        "direction_120": direction_120,
        "label_60": label_60,
        "label_120": label_120,
        "signal_strength": signal_strength,
        "condition_agreement": agreement,
        "evidence_coverage": evidence_coverage,
        "high_confidence": high_confidence,
        "alert": alert,
        "level": level,
        "reasons": reasons[:3],
        "score": score,
        "metrics": {
            "buy_pressure_pct": buy_pressure,
            "sell_pressure_pct": 100.0 - buy_pressure,
            "buy_pressure10_pct": buy_pressure10,
            "pressure_change_pct": pressure_change,
            "change_10s_pct": r10,
            "change_30s_pct": r30,
            "change_60s_pct": r60,
            "change_120s_pct": r120,
            "recent_vwap": recent_vwap,
            "above_vwap_pct": above_vwap_pct,
            "microprice_bias_pct": micro_bias_pct,
            "spread_pct": spread_pct,
            "coverage_seconds": coverage_seconds,
            "age_seconds": age_seconds,
            "market_open": market_open,
            "l2_true": l2_true,
            "l2_periods": l2_periods,
            "l2_order_buy_pct": l2_order_buy_pct,
            "l2_sell_cancel_pct": l2_sell_cancel_pct,
            "l2_total_bid_pct": l2_total_bid_pct,
            "l2_queue_buy_pct": l2_queue_buy_pct,
            "validated_l2_high_accuracy_60": high_acc,
            "validated_l2_high_samples_60": high_samples,
            "validated_all_accuracy_60": all_acc,
            "validated_all_samples_60": all_samples,
            **flow,
        },
    }
