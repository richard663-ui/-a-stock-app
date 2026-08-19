# -*- coding: utf-8 -*-
"""Persistent local bridge: Guosheng QMT -> Supabase -> Streamlit Cloud.

Read-only: this module never imports xttrader and never sends orders.
V18 also streams documented Level-2 market data when the broker entitlement is
available; unavailable L2 periods degrade gracefully to the existing tick feed.
"""
from __future__ import annotations

import math
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xtquant import xtdata

from modules.cloud_bridge import CloudBridge, load_bridge_config
from modules.qmt_level2 import QMTLevel2Manager


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [_clean(x) for x in value]
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
    return values[0] if isinstance(values, (list, tuple)) and values else default


def _iso_time(value: Any) -> str:
    if isinstance(value, (pd.Timestamp, datetime)):
        return pd.Timestamp(value).to_pydatetime().isoformat(timespec="milliseconds")
    text = str(value or "").strip()
    if text:
        for fmt in (
            "%Y%m%d %H:%M:%S.%f", "%Y%m%d %H:%M:%S",
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
    for key in ("time", "timetag", "datetime", "date"):
        value = tick.get(key)
        if value not in (None, ""):
            return _iso_time(value)
    return _iso_time(fallback)


def normalize_tick(symbol: str, tick: Dict[str, Any], fallback_time: Any = None) -> Dict[str, Any]:
    row = {
        "symbol": symbol,
        "captured_at": _captured_time(tick, fallback_time),
        "time": tick.get("time"),
        "timetag": tick.get("timetag"),
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


def main() -> None:
    config = load_bridge_config()
    if not config.ok:
        raise SystemExit("Bridge configuration is incomplete. Run repair_and_start_bridge.bat once.")

    bridge = CloudBridge(config)
    l2 = QMTLevel2Manager()
    current_symbol = ""
    subscription_id: Optional[int] = None
    rows: deque = deque(maxlen=600)
    last_publish = 0.0
    last_l2_publish = 0.0
    last_print = 0.0

    print("QMT cloud bridge V18 started. Read-only mode.")
    print(f"Bridge ID: {config.bridge_id}")

    while True:
        try:
            requested = (bridge.get_requested_symbol() or current_symbol or "000400.SZ").upper()
            if requested != current_symbol:
                if subscription_id is not None:
                    try:
                        xtdata.unsubscribe_quote(subscription_id)
                    except Exception:
                        pass
                current_symbol = requested
                rows.clear()
                print(f"Switching to {current_symbol}")
                try:
                    subscription_id = xtdata.subscribe_quote(current_symbol, period="tick", count=-1)
                except Exception as exc:
                    subscription_id = None
                    print(f"Live tick subscription warning: {exc}")
                time.sleep(0.5)
                for item in backfill_ticks(current_symbol):
                    _append_unique(rows, item)
                if rows:
                    bridge.publish_ticks(current_symbol, list(rows), status="loading")
                    print(f"Backfilled {len(rows)} ticks")

                l2_status = l2.switch(current_symbol)
                caps = l2_status.get("capabilities", {})
                available = [name for name, item in caps.items() if item.get("available")]
                print("Level-2 available: " + (", ".join(available) if available else "none yet"))

            data = xtdata.get_full_tick([current_symbol]) or {}
            tick = data.get(current_symbol) or {}
            if tick:
                _append_unique(rows, normalize_tick(current_symbol, tick))

            now = time.time()
            if now - last_publish >= 1.0:
                bridge.publish_ticks(current_symbol, list(rows), status="online")
                last_publish = now

            if now - last_l2_publish >= 1.0:
                l2_snapshot = l2.snapshot()
                summary = l2_snapshot.get("summary", {})
                bridge.publish_level2(
                    current_symbol,
                    summary=summary,
                    capabilities=l2_snapshot.get("capabilities", {}),
                    recent_transactions=l2_snapshot.get("recent_transactions", []),
                    recent_orders=l2_snapshot.get("recent_orders", []),
                    quoteaux=l2_snapshot.get("quoteaux", {}),
                    orderqueue=l2_snapshot.get("orderqueue", {}),
                    status="online" if summary.get("ok") else "waiting_l2",
                )
                last_l2_publish = now

            if now - last_print >= 5.0:
                latest = rows[-1] if rows else {}
                l2_snapshot = l2.snapshot()
                s = l2_snapshot.get("summary", {})
                print(
                    f"{datetime.now().strftime('%H:%M:%S')} {current_symbol} "
                    f"price={latest.get('lastPrice')} samples={len(rows)} "
                    f"L2={s.get('direction', 'WATCH')} agreement={s.get('agreement', 0)}%"
                )
                last_print = now
        except KeyboardInterrupt:
            print("Bridge stopped")
            l2.stop()
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
