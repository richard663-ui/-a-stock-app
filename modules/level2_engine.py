# -*- coding: utf-8 -*-
"""Real Level-2 analytics for XtQuant/QMT.

Inputs are documented XtData Level-2 feeds. Calculations are deliberately
transparent and degrade independently when a broker feed/field is unavailable.
No trading calls live in this module.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Sequence

import math


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _sum_value(value: Any) -> float:
    if isinstance(value, (list, tuple)):
        return sum(_f(x) for x in value)
    return _f(value)


def _list_f(value: Any) -> List[float]:
    return [_f(x) for x in value] if isinstance(value, (list, tuple)) else []


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _ts_ms(row: Mapping[str, Any]) -> int:
    value = row.get("time") or row.get("timestamp") or row.get("stime") or 0
    try:
        x = float(value)
        if x > 0:
            if x < 10_000_000_000:
                x *= 1000.0
            return int(x)
    except Exception:
        pass
    text = str(value or "").strip()
    if not text:
        return 0
    for fmt in (
        "%Y%m%d%H%M%S.%f", "%Y%m%d%H%M%S",
        "%Y%m%d %H:%M:%S.%f", "%Y%m%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return int(datetime.strptime(text, fmt).timestamp() * 1000)
        except Exception:
            pass
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except Exception:
        return 0


def _recent(rows: Sequence[Mapping[str, Any]], seconds: int = 60) -> List[Mapping[str, Any]]:
    if not rows:
        return []
    stamped = [(r, _ts_ms(r)) for r in rows]
    valid = [x for x in stamped if x[1] > 0]
    if not valid:
        return list(rows[-120:])
    end = max(ts for _, ts in valid)
    start = end - int(seconds) * 1000
    return [r for r, ts in valid if start <= ts <= end]


def _amount(row: Mapping[str, Any]) -> float:
    amount = _f(row.get("amount"))
    return amount if amount > 0 else _f(row.get("price")) * _f(row.get("volume"))


def _pct(part: float, total: float, default: float = 50.0) -> float:
    return part / total * 100.0 if total > 0 else default


def _latest(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return rows[-1] if rows else {}


def _category_total(row: Mapping[str, Any], side: str, suffix: str = "") -> float:
    total = _f(row.get(f"{side}TotalAmount{suffix}"))
    if total > 0:
        return total
    return sum(_f(row.get(f"{side}{bucket}Amount{suffix}")) for bucket in ("Most", "Big", "Medium", "Small"))


def _tc_window_amount(rows: Sequence[Mapping[str, Any]], side: str) -> float:
    """Return amount attributable to this analysis window, never all-day total."""
    if not rows:
        return 0.0
    dx = sum(_category_total(row, side, "Dx") for row in rows)
    if dx > 0:
        return dx
    if len(rows) >= 2:
        first = _category_total(rows[0], side)
        last = _category_total(rows[-1], side)
        return max(0.0, last - first)
    return 0.0


def _tc_window_bucket(rows: Sequence[Mapping[str, Any]], side: str, bucket: str) -> float:
    if not rows:
        return 0.0
    dx = sum(_f(row.get(f"{side}{bucket}AmountDx")) for row in rows)
    if dx > 0:
        return dx
    if len(rows) >= 2:
        first = _f(rows[0].get(f"{side}{bucket}Amount"))
        last = _f(rows[-1].get(f"{side}{bucket}Amount"))
        return max(0.0, last - first)
    return 0.0


def _depth10_pressure(row: Mapping[str, Any]) -> tuple[float, float, float]:
    bids = _list_f(row.get("bidVol"))[:10]
    asks = _list_f(row.get("askVol"))[:10]
    if not bids and not asks:
        return 0.0, 0.0, 50.0
    weights = [1.00, .90, .80, .70, .62, .54, .47, .40, .34, .28]
    b = sum((bids[i] if i < len(bids) else 0.0) * weights[i] for i in range(10))
    a = sum((asks[i] if i < len(asks) else 0.0) * weights[i] for i in range(10))
    return b, a, _pct(b, b + a)


def analyze_level2(
    *,
    quotes: Sequence[Mapping[str, Any]] | None = None,
    transactions: Sequence[Mapping[str, Any]] | None = None,
    orders: Sequence[Mapping[str, Any]] | None = None,
    quoteaux: Sequence[Mapping[str, Any]] | None = None,
    transactioncount: Sequence[Mapping[str, Any]] | None = None,
    orderqueue: Sequence[Mapping[str, Any]] | None = None,
    window_seconds: int = 60,
) -> Dict[str, Any]:
    qt = _recent(list(quotes or []), window_seconds)
    tx = _recent(list(transactions or []), window_seconds)
    od = _recent(list(orders or []), window_seconds)
    qa = _recent(list(quoteaux or []), window_seconds)
    tc = _recent(list(transactioncount or []), window_seconds)
    oq = _recent(list(orderqueue or []), window_seconds)

    available = {
        "l2quote": bool(qt),
        "l2transaction": bool(tx),
        "l2order": bool(od),
        "l2quoteaux": bool(qa),
        "l2transactioncount": bool(tc),
        "l2orderqueue": bool(oq),
    }

    # Real aggressive trades from l2transaction.
    active_buy = sum(_amount(r) for r in tx if _i(r.get("tradeFlag")) == 1)
    active_sell = sum(_amount(r) for r in tx if _i(r.get("tradeFlag")) == 2)
    tx_cancel_count = sum(1 for r in tx if _i(r.get("tradeFlag")) == 3)

    # When transaction-count is available, aggregate its incremental fields over
    # the whole 60s window; if only cumulative fields exist, use last-first.
    tc_buy = _tc_window_amount(tc, "bid")
    tc_sell = _tc_window_amount(tc, "off")
    if tc_buy + tc_sell > 0:
        active_buy, active_sell = tc_buy, tc_sell

    directional_amount = active_buy + active_sell
    active_buy_pct = _pct(active_buy, directional_amount)
    active_net = active_buy - active_sell

    big_buy = _tc_window_bucket(tc, "bid", "Big") + _tc_window_bucket(tc, "bid", "Most")
    big_sell = _tc_window_bucket(tc, "off", "Big") + _tc_window_bucket(tc, "off", "Most")
    big_total = big_buy + big_sell
    big_buy_pct = _pct(big_buy, big_total)
    big_net = big_buy - big_sell
    most_buy = _tc_window_bucket(tc, "bid", "Most")
    most_sell = _tc_window_bucket(tc, "off", "Most")

    buy_order_vol = sum(_f(r.get("volume")) for r in od if _i(r.get("entrustDirection")) == 1)
    sell_order_vol = sum(_f(r.get("volume")) for r in od if _i(r.get("entrustDirection")) == 2)
    cancel_buy_vol = sum(_f(r.get("volume")) for r in od if _i(r.get("entrustDirection")) == 3)
    cancel_sell_vol = sum(_f(r.get("volume")) for r in od if _i(r.get("entrustDirection")) == 4)

    qa_first, qa_last = (qa[0], qa[-1]) if qa else ({}, {})
    withdraw_buy_delta = max(0.0, _f(qa_last.get("withdrawBidAmount")) - _f(qa_first.get("withdrawBidAmount")))
    withdraw_sell_delta = max(0.0, _f(qa_last.get("withdrawOffAmount")) - _f(qa_first.get("withdrawOffAmount")))
    total_bid = _f(qa_last.get("totalBidQuantity"))
    total_offer = _f(qa_last.get("totalOffQuantity"))
    total_book_buy_pct = _pct(total_bid, total_bid + total_offer)
    if withdraw_buy_delta + withdraw_sell_delta <= 0 and cancel_buy_vol + cancel_sell_vol > 0:
        withdraw_buy_delta = cancel_buy_vol
        withdraw_sell_delta = cancel_sell_vol
    cancel_sell_support_pct = _pct(withdraw_sell_delta, withdraw_buy_delta + withdraw_sell_delta)

    oq_last = _latest(oq)
    qbid = _sum_value(oq_last.get("bidLevelVolume"))
    qoff = _sum_value(oq_last.get("offerLevelVolume"))
    queue_buy_pct = _pct(qbid, qbid + qoff)

    quote_last = _latest(qt)
    depth10_bid, depth10_offer, depth10_buy_pct = _depth10_pressure(quote_last)

    up_votes = 0
    down_votes = 0
    reasons_up: List[str] = []
    reasons_down: List[str] = []

    if directional_amount > 0:
        if active_buy_pct >= 62:
            up_votes += 2; reasons_up.append(f"主动买入占{active_buy_pct:.0f}%")
        elif active_buy_pct <= 38:
            down_votes += 2; reasons_down.append(f"主动卖出占{100-active_buy_pct:.0f}%")

    if big_total > 0:
        if big_buy_pct >= 62:
            up_votes += 2; reasons_up.append("大/特大单净流入")
        elif big_buy_pct <= 38:
            down_votes += 2; reasons_down.append("大/特大单净流出")

    order_total = buy_order_vol + sell_order_vol
    order_buy_pct = _pct(buy_order_vol, order_total) if order_total > 0 else 50.0
    if order_total > 0:
        if order_buy_pct >= 60:
            up_votes += 1; reasons_up.append("新增买单明显更多")
        elif order_buy_pct <= 40:
            down_votes += 1; reasons_down.append("新增卖单明显更多")

    if withdraw_buy_delta + withdraw_sell_delta > 0:
        if cancel_sell_support_pct >= 62:
            up_votes += 1; reasons_up.append("撤卖明显多于撤买")
        elif cancel_sell_support_pct <= 38:
            down_votes += 1; reasons_down.append("撤买明显多于撤卖")

    if total_bid + total_offer > 0:
        if total_book_buy_pct >= 58:
            up_votes += 1; reasons_up.append("全盘口委买量占优")
        elif total_book_buy_pct <= 42:
            down_votes += 1; reasons_down.append("全盘口委卖量占优")

    if depth10_bid + depth10_offer > 0:
        if depth10_buy_pct >= 60:
            up_votes += 1; reasons_up.append("L2十档买盘承接更强")
        elif depth10_buy_pct <= 40:
            down_votes += 1; reasons_down.append("L2十档卖盘压力更强")

    if qbid + qoff > 0:
        if queue_buy_pct >= 60:
            up_votes += 1; reasons_up.append("买一队列承接更强")
        elif queue_buy_pct <= 40:
            down_votes += 1; reasons_down.append("卖一队列压单更强")

    total_votes = up_votes + down_votes
    if total_votes <= 0:
        direction, agreement = "WATCH", 0
    elif up_votes > down_votes:
        direction, agreement = "UP", round(up_votes / total_votes * 100)
    elif down_votes > up_votes:
        direction, agreement = "DOWN", round(down_votes / total_votes * 100)
    else:
        direction, agreement = "WATCH", 50

    if agreement < 80 or max(up_votes, down_votes) < 4:
        direction = "WATCH"

    tc_last = _latest(tc)
    fund_label = "真实资金偏流入" if active_buy_pct >= 60 else "真实资金偏流出" if active_buy_pct <= 40 else "真实资金暂均衡"
    large_label = "大单偏买" if big_buy_pct >= 60 else "大单偏卖" if big_buy_pct <= 40 else "大单均衡"
    cancel_label = "撤卖更多，偏强" if cancel_sell_support_pct >= 60 else "撤买更多，偏弱" if cancel_sell_support_pct <= 40 else "撤单均衡"
    reasons = reasons_up if direction == "UP" else reasons_down if direction == "DOWN" else (reasons_up + reasons_down)

    return {
        "ok": any(available.values()),
        "window_seconds": int(window_seconds),
        "available": available,
        "direction": direction,
        "agreement": int(agreement),
        "up_votes": int(up_votes),
        "down_votes": int(down_votes),
        "fund_label": fund_label,
        "large_label": large_label,
        "cancel_label": cancel_label,
        "reasons": reasons[:4],
        "metrics": {
            "active_buy_amount": active_buy,
            "active_sell_amount": active_sell,
            "active_net_amount": active_net,
            "active_buy_pct": active_buy_pct,
            "big_buy_amount": big_buy,
            "big_sell_amount": big_sell,
            "big_net_amount": big_net,
            "big_buy_pct": big_buy_pct,
            "most_buy_amount": most_buy,
            "most_sell_amount": most_sell,
            "buy_order_volume": buy_order_vol,
            "sell_order_volume": sell_order_vol,
            "order_buy_pct": order_buy_pct,
            "withdraw_buy": withdraw_buy_delta,
            "withdraw_sell": withdraw_sell_delta,
            "cancel_sell_support_pct": cancel_sell_support_pct,
            "total_bid_quantity": total_bid,
            "total_offer_quantity": total_offer,
            "total_book_buy_pct": total_book_buy_pct,
            "depth10_bid_weighted": depth10_bid,
            "depth10_offer_weighted": depth10_offer,
            "depth10_buy_pct": depth10_buy_pct,
            "queue_bid_volume": qbid,
            "queue_offer_volume": qoff,
            "queue_buy_pct": queue_buy_pct,
            "ddx": _f(tc_last.get("ddx")),
            "ddy": _f(tc_last.get("ddy")),
            "ddz": _f(tc_last.get("ddz")),
            "net_order": _f(tc_last.get("netOrder")),
            "net_withdraw": _f(tc_last.get("netWithdraw")),
            "transaction_events": len(tx),
            "order_events": len(od),
            "transaction_cancel_events": tx_cancel_count,
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
