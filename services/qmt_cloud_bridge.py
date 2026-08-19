# -*- coding: utf-8 -*-
"""Persistent local bridge: Guosheng QMT -> Supabase -> Streamlit Cloud.

Read-only by design: this module imports xtdata only, never xttrader, and never
submits orders.

V17.2 adds a real Level-2 path when the Guosheng/QMT entitlement exposes it:
- l2quote: depth snapshot
- l2transaction: active buy/sell trades
- l2order: order-side flow
- l2quoteaux: total bid/offer and withdrawal totals
- l2orderqueue: best-level queue information

Those streams are reduced locally into compact 60-second microstructure
features. Raw Level-2 is not uploaded to Supabase, keeping the cloud payload
small. If a Level-2 period is unavailable the bridge keeps running and the
1-minute engine falls back to snapshot/tick features.
"""
from __future__ import annotations

import math
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xtquant import xtdata

from modules.cloud_bridge import CloudBridge, load_bridge_config
from modules.prediction_journal import PredictionJournal
from modules.short_direction import analyze_short_direction

L2_PERIODS = (
    "l2quote",
    "l2transaction",
    "l2order",
    "l2quoteaux",
    "l2orderqueue",
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_clean(x) for x in list(value)]
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    try:
        return _clean(value.item())
    except Exception:
        pass
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def _first(values, default=None):
    return values[0] if isinstance(values, (list, tuple, np.ndarray)) and len(values) else default


def _sum_numeric(value: Any) -> float:
    if isinstance(value, (list, tuple, np.ndarray)):
        return float(sum(_safe_float(x) for x in value))
    return _safe_float(value)


def _iso_time(value: Any) -> str:
    if isinstance(value, (pd.Timestamp, datetime)):
        return pd.Timestamp(value).to_pydatetime().isoformat(timespec="milliseconds")
    text = str(value or "").strip()
    if text:
        for fmt in (
            "%Y%m%d %H:%M:%S.%f", "%Y%m%d %H:%M:%S",
            "%Y%m%d%H%M%S.%f", "%Y%m%d%H%M%S",
            "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
        ):
            try:
                return datetime.strptime(text, fmt).isoformat(timespec="milliseconds")
            except Exception:
                pass
    try:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        if number > 1_000_000_000:
            return datetime.fromtimestamp(number).isoformat(timespec="milliseconds")
    except Exception:
        pass
    return datetime.now().isoformat(timespec="milliseconds")


def _captured_time(tick: Dict[str, Any], fallback: Any = None) -> str:
    captured = tick.get("captured_at")
    if captured:
        return _iso_time(captured)
    for key in ("time", "timetag", "stime", "datetime", "date"):
        value = tick.get(key)
        if value not in (None, ""):
            return _iso_time(value)
    return _iso_time(fallback)


def normalize_tick(symbol: str, tick: Dict[str, Any], fallback_time: Any = None) -> Dict[str, Any]:
    row = {
        "symbol": symbol,
        "captured_at": _captured_time(tick, fallback_time),
        "time": tick.get("time"),
        "timetag": tick.get("timetag") or tick.get("stime"),
        "lastPrice": tick.get("lastPrice") or tick.get("price"),
        "open": tick.get("open"),
        "high": tick.get("high"),
        "low": tick.get("low"),
        "lastClose": tick.get("lastClose") or tick.get("preClose"),
        "avgPrice": tick.get("avgPrice"),
        "amount": tick.get("amount"),
        "volume": tick.get("volume"),
        "bidPrice": list(tick.get("bidPrice") or []),
        "askPrice": list(tick.get("askPrice") or []),
        "bidVol": list(tick.get("bidVol") or []),
        "askVol": list(tick.get("askVol") or []),
        "bidPrice1": _first(tick.get("bidPrice")),
        "askPrice1": _first(tick.get("askPrice")),
        "bidVol1": _first(tick.get("bidVol")),
        "askVol1": _first(tick.get("askVol")),
    }
    return _clean(row)


def _records_from_frame(symbol: str, frame: Any) -> Iterable[Dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    records: List[Dict[str, Any]] = []
    for idx, row in frame.tail(300).iterrows():
        raw = row.to_dict()
        raw.setdefault("timetag", str(idx))
        records.append(normalize_tick(symbol, raw, fallback_time=idx))
    records.sort(key=lambda item: str(item.get("captured_at") or ""))
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for item in records:
        key = (item.get("captured_at"), item.get("lastPrice"), item.get("volume"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def backfill_ticks(symbol: str) -> List[Dict[str, Any]]:
    try:
        result = xtdata.get_market_data_ex(
            field_list=[], stock_list=[symbol], period="tick",
            start_time="", end_time="", count=300,
            dividend_type="none", fill_data=False,
        ) or {}
        return list(_records_from_frame(symbol, result.get(symbol)))
    except Exception as exc:
        print(f"Backfill unavailable for {symbol}: {exc}")
        return []


def _append_unique(rows: deque, row: Dict[str, Any]) -> None:
    if not row:
        return
    if not rows:
        rows.append(row)
        return
    latest = rows[-1]
    if (
        row.get("captured_at") != latest.get("captured_at")
        or row.get("lastPrice") != latest.get("lastPrice")
        or row.get("volume") != latest.get("volume")
    ):
        rows.append(row)
    else:
        # Same market snapshot can still contain fresher L2 aggregates.
        latest.update(row)


def _fetch_frame(symbol: str, period: str, count: int) -> pd.DataFrame:
    try:
        result = xtdata.get_market_data_ex(
            field_list=[], stock_list=[symbol], period=period,
            start_time="", end_time="", count=count,
            dividend_type="none", fill_data=False,
        ) or {}
        frame = result.get(symbol)
        return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _frame_epoch_seconds(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=float)
    if "time" in frame.columns:
        raw = pd.to_numeric(frame["time"], errors="coerce")
        if raw.notna().any():
            median = float(raw.dropna().median())
            if median > 10_000_000_000:
                raw = raw / 1000.0
            return raw
    parsed = pd.to_datetime(frame.index.astype(str), errors="coerce")
    try:
        return parsed.map(lambda x: x.timestamp() if pd.notna(x) else np.nan)
    except Exception:
        return pd.Series(index=frame.index, dtype=float)


def _recent(frame: pd.DataFrame, seconds: int = 65) -> pd.DataFrame:
    if frame.empty:
        return frame
    epochs = _frame_epoch_seconds(frame)
    if epochs.notna().any():
        cutoff = time.time() - seconds
        recent = frame.loc[epochs >= cutoff]
        if not recent.empty:
            return recent
    return frame.tail(min(len(frame), 1000))


def _last_dict(frame: pd.DataFrame) -> Dict[str, Any]:
    if frame.empty:
        return {}
    try:
        return frame.iloc[-1].to_dict()
    except Exception:
        return {}


def _lots_from_shares(value: float) -> float:
    # Official QMT examples show l2transaction volume as shares while tick
    # cumulative volume for A-shares is in lots. Convert L2 event volume to lots.
    return max(0.0, _safe_float(value)) / 100.0


def collect_l2_features(symbol: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return compact 60-second Level-2 features and latest L2 quote override."""
    quote = _fetch_frame(symbol, "l2quote", 8)
    transactions = _recent(_fetch_frame(symbol, "l2transaction", 5000), 65)
    orders = _recent(_fetch_frame(symbol, "l2order", 5000), 65)
    aux = _recent(_fetch_frame(symbol, "l2quoteaux", 80), 70)
    queue = _recent(_fetch_frame(symbol, "l2orderqueue", 40), 70)

    available = []
    if not quote.empty:
        available.append("l2quote")
    if not transactions.empty:
        available.append("l2transaction")
    if not orders.empty:
        available.append("l2order")
    if not aux.empty:
        available.append("l2quoteaux")
    if not queue.empty:
        available.append("l2orderqueue")

    # True active trade side: QMT tradeFlag 1=outer/active buy, 2=inner/active sell,
    # 3=cancel on Shenzhen. Cancellation rows are excluded from trade volume.
    tx_buy_lots = tx_sell_lots = 0.0
    tx_events = 0
    thousand_buy = thousand_sell = ten_thousand_buy = ten_thousand_sell = 0
    if not transactions.empty and "volume" in transactions.columns:
        flags = pd.to_numeric(transactions.get("tradeFlag"), errors="coerce")
        shares = pd.to_numeric(transactions["volume"], errors="coerce").fillna(0).clip(lower=0)
        lots = shares / 100.0
        buy_mask = flags.eq(1)
        sell_mask = flags.eq(2)
        tx_buy_lots = float(lots[buy_mask].sum())
        tx_sell_lots = float(lots[sell_mask].sum())
        tx_events = int((buy_mask | sell_mask).sum())
        thousand_buy = int((buy_mask & lots.ge(1000)).sum())
        thousand_sell = int((sell_mask & lots.ge(1000)).sum())
        ten_thousand_buy = int((buy_mask & lots.ge(10000)).sum())
        ten_thousand_sell = int((sell_mask & lots.ge(10000)).sum())

    tx_directional = tx_buy_lots + tx_sell_lots
    tx_buy_pct = tx_buy_lots / tx_directional * 100.0 if tx_directional > 0 else 50.0

    order_buy_lots = order_sell_lots = cancel_buy_lots = cancel_sell_lots = 0.0
    order_events = 0
    if not orders.empty and "volume" in orders.columns:
        direction = pd.to_numeric(orders.get("entrustDirection"), errors="coerce")
        shares = pd.to_numeric(orders["volume"], errors="coerce").fillna(0).clip(lower=0)
        lots = shares / 100.0
        order_buy_lots = float(lots[direction.eq(1)].sum())
        order_sell_lots = float(lots[direction.eq(2)].sum())
        cancel_buy_lots = float(lots[direction.eq(3)].sum())
        cancel_sell_lots = float(lots[direction.eq(4)].sum())
        order_events = int(direction.isin([1, 2, 3, 4]).sum())

    order_directional = order_buy_lots + order_sell_lots
    order_buy_pct = order_buy_lots / order_directional * 100.0 if order_directional > 0 else 50.0

    total_bid_lots = total_ask_lots = 0.0
    withdraw_bid_lots = withdraw_ask_lots = 0.0
    if not aux.empty:
        latest_aux = _last_dict(aux)
        total_bid_lots = _lots_from_shares(latest_aux.get("totalBidQuantity"))
        total_ask_lots = _lots_from_shares(latest_aux.get("totalOffQuantity"))
        if len(aux) >= 2:
            first_aux = aux.iloc[0].to_dict()
            withdraw_bid_lots = _lots_from_shares(
                max(0.0, _safe_float(latest_aux.get("withdrawBidQuantity")) - _safe_float(first_aux.get("withdrawBidQuantity")))
            )
            withdraw_ask_lots = _lots_from_shares(
                max(0.0, _safe_float(latest_aux.get("withdrawOffQuantity")) - _safe_float(first_aux.get("withdrawOffQuantity")))
            )

    # On Shanghai, directional cancel records are also available in l2order.
    # Use the larger of the directional-event estimate and quoteaux cumulative delta.
    cancel_buy_lots = max(cancel_buy_lots, withdraw_bid_lots)
    cancel_sell_lots = max(cancel_sell_lots, withdraw_ask_lots)
    cancel_total = cancel_buy_lots + cancel_sell_lots
    # Sell-side cancellation is bullish; buy-side cancellation is bearish.
    sell_cancel_pct = cancel_sell_lots / cancel_total * 100.0 if cancel_total > 0 else 50.0

    queue_bid_lots = queue_ask_lots = 0.0
    if not queue.empty:
        latest_queue = _last_dict(queue)
        queue_bid_lots = _lots_from_shares(_sum_numeric(latest_queue.get("bidLevelVolume")))
        queue_ask_lots = _lots_from_shares(_sum_numeric(latest_queue.get("offerLevelVolume")))
    queue_total = queue_bid_lots + queue_ask_lots
    queue_buy_pct = queue_bid_lots / queue_total * 100.0 if queue_total > 0 else 50.0

    total_book = total_bid_lots + total_ask_lots
    total_bid_pct = total_bid_lots / total_book * 100.0 if total_book > 0 else 50.0

    true_l2 = bool(
        not transactions.empty
        or not orders.empty
        or not aux.empty
        or not queue.empty
    )

    features = {
        "l2_true": true_l2,
        "l2_periods": available,
        "l2_trade_events_60s": tx_events,
        "l2_trade_buy_lots_60s": tx_buy_lots,
        "l2_trade_sell_lots_60s": tx_sell_lots,
        "l2_trade_buy_pct_60s": tx_buy_pct,
        "l2_net_active_lots_60s": tx_buy_lots - tx_sell_lots,
        "l2_order_events_60s": order_events,
        "l2_order_buy_lots_60s": order_buy_lots,
        "l2_order_sell_lots_60s": order_sell_lots,
        "l2_order_buy_pct_60s": order_buy_pct,
        "l2_cancel_buy_lots_60s": cancel_buy_lots,
        "l2_cancel_sell_lots_60s": cancel_sell_lots,
        "l2_sell_cancel_pct_60s": sell_cancel_pct,
        "l2_total_bid_lots": total_bid_lots,
        "l2_total_ask_lots": total_ask_lots,
        "l2_total_bid_pct": total_bid_pct,
        "l2_queue_bid_lots": queue_bid_lots,
        "l2_queue_ask_lots": queue_ask_lots,
        "l2_queue_buy_pct": queue_buy_pct,
        "l2_thousand_buy": thousand_buy,
        "l2_thousand_sell": thousand_sell,
        "l2_ten_thousand_buy": ten_thousand_buy,
        "l2_ten_thousand_sell": ten_thousand_sell,
    }

    quote_override = _last_dict(quote)
    return _clean(features), _clean(quote_override)


def _subscribe_symbol(symbol: str) -> Tuple[Dict[str, Optional[int]], Dict[str, str]]:
    ids: Dict[str, Optional[int]] = {}
    state: Dict[str, str] = {}
    for period in ("tick",) + L2_PERIODS:
        try:
            sub_id = xtdata.subscribe_quote(symbol, period=period, count=-1)
            ids[period] = sub_id
            state[period] = "ok"
        except Exception as exc:
            ids[period] = None
            state[period] = f"unavailable: {exc}"
    return ids, state


def _unsubscribe_all(ids: Dict[str, Optional[int]]) -> None:
    for sub_id in ids.values():
        if sub_id is None:
            continue
        try:
            xtdata.unsubscribe_quote(sub_id)
        except Exception:
            pass


def main() -> None:
    config = load_bridge_config()
    if not config.ok:
        raise SystemExit("Bridge configuration is incomplete. Run repair_and_start_bridge.bat once.")

    bridge = CloudBridge(config)
    journal = PredictionJournal(PROJECT_ROOT / "runtime" / "one_minute_predictions.sqlite3")
    current_symbol = ""
    subscription_ids: Dict[str, Optional[int]] = {}
    subscription_state: Dict[str, str] = {}
    rows: deque = deque(maxlen=900)
    last_publish = 0.0
    last_print = 0.0
    last_l2_collect = 0.0
    last_stats = 0.0
    latest_l2_features: Dict[str, Any] = {"l2_true": False, "l2_periods": []}
    latest_quote_override: Dict[str, Any] = {}
    validation_stats: Dict[str, Any] = {}

    print("QMT cloud bridge started. Read-only mode.")
    print(f"Bridge ID: {config.bridge_id}")

    while True:
        try:
            requested = (bridge.get_requested_symbol() or current_symbol or "000400.SZ").upper()
            if requested != current_symbol:
                _unsubscribe_all(subscription_ids)
                current_symbol = requested
                rows.clear()
                latest_l2_features = {"l2_true": False, "l2_periods": []}
                latest_quote_override = {}
                validation_stats = journal.stats(current_symbol)
                print(f"Switching to {current_symbol}")
                subscription_ids, subscription_state = _subscribe_symbol(current_symbol)
                for period in ("tick",) + L2_PERIODS:
                    print(f"  {period}: {subscription_state.get(period, 'unknown')}")
                time.sleep(0.8)
                for item in backfill_ticks(current_symbol):
                    _append_unique(rows, item)
                if rows:
                    bridge.publish_ticks(current_symbol, list(rows), status="loading")
                    print(f"Backfilled {len(rows)} ticks")

            now = time.time()
            if now - last_l2_collect >= 1.0:
                try:
                    latest_l2_features, latest_quote_override = collect_l2_features(current_symbol)
                except Exception as exc:
                    latest_l2_features = {"l2_true": False, "l2_periods": [], "l2_error": str(exc)}
                    latest_quote_override = {}
                last_l2_collect = now

            data = xtdata.get_full_tick([current_symbol]) or {}
            tick = dict(data.get(current_symbol) or {})
            if latest_quote_override:
                # l2quote is preferred for depth fields when available.
                for key in (
                    "time", "stime", "lastPrice", "open", "high", "low", "lastClose",
                    "amount", "volume", "bidPrice", "askPrice", "bidVol", "askVol",
                    "transactionNum", "stockStatus",
                ):
                    value = latest_quote_override.get(key)
                    if value not in (None, "", []):
                        tick[key] = value

            if tick:
                row = normalize_tick(current_symbol, tick)
                row.update(latest_l2_features)
                row.update(validation_stats)
                _append_unique(rows, row)

                current_price = _safe_float(row.get("lastPrice"))
                if current_price > 0:
                    journal.mature(symbol=current_symbol, current_price=current_price, now_ts=now)
                    frame = pd.DataFrame(list(rows))
                    short = analyze_short_direction(frame)
                    if short.get("ok") and short.get("live") and short.get("direction_60") in {"UP", "DOWN"}:
                        journal.record(
                            symbol=current_symbol,
                            price=current_price,
                            direction=str(short.get("direction_60")),
                            agreement=int(short.get("condition_agreement", 0) or 0),
                            score=float(short.get("score", 0.0) or 0.0),
                            high_confidence=bool(short.get("high_confidence", False)),
                            true_l2=bool(short.get("metrics", {}).get("l2_true", False)),
                            now_ts=now,
                        )

                    if now - last_stats >= 5.0:
                        stats = journal.stats(current_symbol)
                        validation_stats = {
                            "validated_all_samples_60": stats.get("all_samples", 0),
                            "validated_all_accuracy_60": stats.get("all_accuracy_pct"),
                            "validated_high_samples_60": stats.get("high_conf_samples", 0),
                            "validated_high_accuracy_60": stats.get("high_conf_accuracy_pct"),
                            "validated_l2_high_samples_60": stats.get("true_l2_high_conf_samples", 0),
                            "validated_l2_high_accuracy_60": stats.get("true_l2_high_conf_accuracy_pct"),
                        }
                        if rows:
                            rows[-1].update(validation_stats)
                        last_stats = now

            if now - last_publish >= 1.0:
                l2_state = "L2" if latest_l2_features.get("l2_true") else "snapshot"
                bridge.publish_ticks(current_symbol, list(rows), status=f"online/{l2_state}")
                last_publish = now

            if now - last_print >= 5.0:
                latest = rows[-1] if rows else {}
                l2_periods = ",".join(latest_l2_features.get("l2_periods", [])) or "none"
                high_acc = validation_stats.get("validated_l2_high_accuracy_60")
                high_n = validation_stats.get("validated_l2_high_samples_60", 0)
                validation_text = "n/a" if high_acc is None else f"{high_acc:.1f}%/{high_n}"
                print(
                    f"{datetime.now().strftime('%H:%M:%S')} {current_symbol} "
                    f"price={latest.get('lastPrice')} samples={len(rows)} "
                    f"l2=[{l2_periods}] 60s_high={validation_text}"
                )
                last_print = now
        except KeyboardInterrupt:
            print("Bridge stopped")
            _unsubscribe_all(subscription_ids)
            break
        except Exception as exc:
            print(f"bridge error: {exc}")
            try:
                if current_symbol:
                    bridge.publish_ticks(current_symbol, list(rows), status=f"error: {exc}")
            except Exception:
                pass
            time.sleep(3.0)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
