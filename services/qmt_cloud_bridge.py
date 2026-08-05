# -*- coding: utf-8 -*-
"""Persistent local bridge: QMT on the ROG -> Supabase -> Streamlit Cloud.

Run once with start_cloud_bridge.bat, or install it at Windows logon with
install_cloud_bridge_startup.bat. The process is read-only and never imports
xttrader.
"""
from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, Iterable, List

import pandas as pd
from xtquant import xtdata

from modules.cloud_bridge import CloudBridge, load_bridge_config


def _first(values, default=None):
    return values[0] if isinstance(values, (list, tuple)) and values else default


def _iso_time(value: Any) -> str:
    try:
        x = float(value)
        if x > 10_000_000_000:
            x /= 1000.0
        return datetime.fromtimestamp(x).isoformat(timespec="seconds")
    except Exception:
        return datetime.now().isoformat(timespec="seconds")


def normalize_tick(symbol: str, tick: Dict[str, Any]) -> Dict[str, Any]:
    captured = tick.get("captured_at") or _iso_time(tick.get("time"))
    return {
        "symbol": symbol,
        "captured_at": captured,
        "time": tick.get("time"),
        "timetag": tick.get("timetag"),
        "lastPrice": tick.get("lastPrice"),
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


def _records_from_frame(symbol: str, frame: pd.DataFrame) -> Iterable[Dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    records: List[Dict[str, Any]] = []
    for idx, row in frame.tail(300).iterrows():
        raw = row.to_dict()
        raw.setdefault("timetag", str(idx))
        records.append(normalize_tick(symbol, raw))
    return records


def backfill_ticks(symbol: str) -> List[Dict[str, Any]]:
    """Try to load today's subscribed tick history so the cloud page fills immediately."""
    try:
        xtdata.subscribe_quote(symbol, period="tick", count=-1)
        time.sleep(0.8)
        result = xtdata.get_market_data_ex(
            field_list=[],
            stock_list=[symbol],
            period="tick",
            start_time="",
            end_time="",
            count=300,
            dividend_type="none",
            fill_data=False,
        ) or {}
        frame = result.get(symbol)
        return list(_records_from_frame(symbol, frame))
    except Exception as exc:
        print(f"Backfill unavailable for {symbol}: {exc}")
        return []


def main() -> None:
    config = load_bridge_config()
    if not config.ok:
        raise SystemExit(
            "Bridge secrets missing. Create .streamlit/secrets.toml with "
            "SUPABASE_URL, SUPABASE_SERVICE_KEY and BRIDGE_ID."
        )

    bridge = CloudBridge(config)
    current_symbol = ""
    subscription_id = None
    rows: deque = deque(maxlen=300)
    last_publish = 0.0

    print("QMT cloud bridge started. Read-only mode.")
    print(f"Bridge ID: {config.bridge_id}")

    while True:
        try:
            requested = bridge.get_requested_symbol() or current_symbol or "000400.SZ"
            requested = requested.upper()

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
                except Exception:
                    subscription_id = None
                for item in backfill_ticks(current_symbol):
                    rows.append(item)
                if rows:
                    bridge.publish_ticks(current_symbol, list(rows), status="loading")
                    print(f"Backfilled {len(rows)} ticks")

            data = xtdata.get_full_tick([current_symbol]) or {}
            tick = data.get(current_symbol) or {}
            if tick:
                row = normalize_tick(current_symbol, tick)
                if not rows or row.get("captured_at") != rows[-1].get("captured_at") or row.get("lastPrice") != rows[-1].get("lastPrice"):
                    rows.append(row)

            now = time.time()
            if now - last_publish >= 1.0:
                bridge.publish_ticks(current_symbol, list(rows), status="online")
                last_publish = now
                latest = rows[-1] if rows else {}
                print(
                    f"{datetime.now().strftime('%H:%M:%S')} {current_symbol} "
                    f"price={latest.get('lastPrice')} samples={len(rows)}"
                )

        except KeyboardInterrupt:
            print("Bridge stopped")
            break
        except Exception as exc:
            print(f"bridge error: {exc}")
            try:
                if current_symbol:
                    bridge.publish_ticks(current_symbol, list(rows), status=f"error: {exc}")
            except Exception:
                pass
            time.sleep(3)
            continue

        time.sleep(0.35)


if __name__ == "__main__":
    main()
