# -*- coding: utf-8 -*-
"""Real Level-2 analytics for XtQuant/QMT.

Inputs are recent rows from documented XtData Level-2 feeds:
- l2transaction: tradeFlag 1=主动买, 2=主动卖, 3=撤单(深市)
- l2order: entrustDirection 1=买, 2=卖, 3=撤买, 4=撤卖(沪市)
- l2quoteaux: total bid/offer and cumulative withdrawal fields
- l2transactioncount: official active buy/sell and large-order statistics
- l2orderqueue: best-level queue totals

The module is pure calculation: no broker connection and no trading calls.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Sequence

import math


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _ts_ms(row: Mapping[str, Any]) -> int:
    value = row.get("time") or row.get("timestamp") or 0
    try:
        x = float(value)
        if x < 10_000_000_000:
            x *= 1000.0
        return int(x)
    except Exception:
        return 0


def _recent(rows: Sequence[Mapping[str, Any]], seconds: int = 60) -> List[Mapping[str, Any]]:
    if not rows:
        return []
    stamped = [(r, _ts_ms(r)) for r in rows]
    valid = [x for x in stamped if x[1] > 0]
    if not valid:
        return list(rows[-400:])
    end = max(ts for _, ts in valid)
    start = end - seconds * 1000
    return [r for r, ts in valid if ts >= start]


def _amount(row: Mapping[str, Any]) -> float:
    amount = _f(row.get("amount"))
    if amount > 0:
        return amount
    return _f(row.get("price")) * _f(row.get("volume"))


def _pct(part: float, total: float, default: float = 50.0) -> float:
    return part / total * 100.0 if total > 0 else default


def _latest(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return rows[-1] if rows else {}


def analyze_level2(
    *,
    transactions: Sequence[Mapping[str, Any]] | None = None,
    orders: Sequence[Mapping[str, Any]] | None = None,
    quoteaux: Sequence[Mapping[str, Any]] | None = None,
    transactioncount: Sequence[Mapping[str, Any]] | None = None,
    orderqueue: Sequence[Mapping[str, Any]] | None = None,
    window_seconds: int = 60,
) -> Dict[str, Any]:
    tx = _recent(list(transactions or []), window_seconds)
    od = _recent(list(orders or []), window_seconds)
    qa = _recent(list(quoteaux or []), window_seconds)
    tc = _recent(list(transactioncount or []), window_seconds)
    oq = _recent(list(orderqueue or []), window_seconds)

    available = {
        "l2transaction": bool(tx),
        "l2order": bool(od),
        "l2quoteaux": bool(qa),
        "l2transactioncount": bool(tc),
        "l2orderqueue": bool(oq),
    }

    # 1) Real aggressive trades from l2transaction.
    active_buy = sum(_amount(r) for r in tx if _i(r.get("tradeFlag")) == 1)
    active_sell = sum(_amount(r) for r in tx if _i(r.get("tradeFlag")) == 2)
    tx_cancel_count = sum(1 for r in tx if _i(r.get("tradeFlag")) == 3)

    # Prefer official l2transactioncount incremental money when available.
    tc_last = _latest(tc)
    tc_buy = _f(tc_last.get("bidTotalAmountDx"))
    tc_sell = _f(tc_last.get("offTotalAmountDx"))
    if tc_buy + tc_sell <= 0:
        tc_buy = _f(tc_last.get("bidTotalAmount"))
        tc_sell = _f(tc_last.get("offTotalAmount"))
    if tc_buy + tc_sell > 0:
        active_buy, active_sell = tc_buy, tc_sell

    directional_amount = active_buy + active_sell
    active_buy_pct = _pct(active_buy, directional_amount)
    active_net = active_buy - active_sell

    # 2) Official big/most active money from l2transactioncount.
    big_buy = _f(tc_last.get("bidBigAmountDx")) + _f(tc_last.get("bidMostAmountDx"))
    big_sell = _f(tc_last.get("offBigAmountDx")) + _f(tc_last.get("offMostAmountDx"))
    if big_buy + big_sell <= 0:
        big_buy = _f(tc_last.get("bidBigAmount")) + _f(tc_last.get("bidMostAmount"))
        big_sell = _f(tc_last.get("offBigAmount")) + _f(tc_last.get("offMostAmount"))
    big_total = big_buy + big_sell
    big_buy_pct = _pct(big_buy, big_total)
    big_net = big_buy - big_sell

    most_buy = _f(tc_last.get("bidMostAmountDx")) or _f(tc_last.get("bidMostAmount"))
    most_sell = _f(tc_last.get("offMostAmountDx")) or _f(tc_last.get("offMostAmount"))

    # 3) Real order submissions/cancellations from l2order.
    buy_order_vol = sum(_f(r.get("volume")) for r in od if _i(r.get("entrustDirection")) == 1)
    sell_order_vol = sum(_f(r.get("volume")) for r in od if _i(r.get("entrustDirection")) == 2)
    cancel_buy_vol = sum(_f(r.get("volume")) for r in od if _i(r.get("entrustDirection")) == 3)
    cancel_sell_vol = sum(_f(r.get("volume")) for r in od if _i(r.get("entrustDirection")) == 4)

    # 4) Quoteaux is cumulative intraday, so use deltas inside the window.
    qa_first, qa_last = (qa[0], qa[-1]) if qa else ({}, {})
    withdraw_buy_delta = max(0.0, _f(qa_last.get("withdrawBidAmount")) - _f(qa_first.get("withdrawBidAmount")))
    withdraw_sell_delta = max(0.0, _f(qa_last.get("withdrawOffAmount")) - _f(qa_first.get("withdrawOffAmount")))
    total_bid = _f(qa_last.get("totalBidQuantity"))
    total_offer = _f(qa_last.get("totalOffQuantity"))
    total_book_buy_pct = _pct(total_bid, total_bid + total_offer)

    # If quoteaux is unavailable on Shanghai, explicit order-cancel directions still help.
    if withdraw_buy_delta + withdraw_sell_delta <= 0 and cancel_buy_vol + cancel_sell_vol > 0:
        withdraw_buy_delta = cancel_buy_vol
        withdraw_sell_delta = cancel_sell_vol
    # More cancel-buy than cancel-sell is bearish; vice versa bullish.
    cancel_sell_support_pct = _pct(withdraw_sell_delta, withdraw_buy_delta + withdraw_sell_delta)

    # 5) Best-level queue pressure.
    oq_last = _latest(oq)
    qbid = _f(oq_last.get("bidLevelVolume"))
    qoff = _f(oq_last.get("offerLevelVolume"))
    queue_buy_pct = _pct(qbid, qbid + qoff)

    # Human-facing score. Require independent families instead of one giant metric.
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
    if order_total > 0:
        order_buy_pct = _pct(buy_order_vol, order_total)
        if order_buy_pct >= 60:
            up_votes += 1; reasons_up.append("新增买单明显更多")
        elif order_buy_pct <= 40:
            down_votes += 1; reasons_down.append("新增卖单明显更多")
    else:
        order_buy_pct = 50.0

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

    if qbid + qoff > 0:
        if queue_buy_pct >= 60:
            up_votes += 1; reasons_up.append("买一队列承接更强")
        elif queue_buy_pct <= 40:
            down_votes += 1; reasons_down.append("卖一队列压单更强")

    total_votes = up_votes + down_votes
    if total_votes <= 0:
        direction, agreement = "WATCH", 0
    elif up_votes > down_votes:
        direction = "UP"
        agreement = round(up_votes / total_votes * 100)
    elif down_votes > up_votes:
        direction = "DOWN"
        agreement = round(down_votes / total_votes * 100)
    else:
        direction, agreement = "WATCH", 50

    strong = agreement >= 80 and max(up_votes, down_votes) >= 4
    if not strong:
        direction = "WATCH"

    if active_buy_pct >= 60:
        fund_label = "真实资金偏流入"
    elif active_buy_pct <= 40:
        fund_label = "真实资金偏流出"
    else:
        fund_label = "真实资金暂均衡"

    if big_buy_pct >= 60:
        large_label = "大单偏买"
    elif big_buy_pct <= 40:
        large_label = "大单偏卖"
    else:
        large_label = "大单均衡"

    if cancel_sell_support_pct >= 60:
        cancel_label = "撤卖更多，偏强"
    elif cancel_sell_support_pct <= 40:
        cancel_label = "撤买更多，偏弱"
    else:
        cancel_label = "撤单均衡"

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
