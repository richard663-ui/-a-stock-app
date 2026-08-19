# -*- coding: utf-8 -*-
"""Probe Guosheng QMT/XtQuant Level-2 capabilities without trading.

Usage:
    py services/qmt_capability_probe.py 000400.SZ [wait_seconds]

The probe checks both cached intraday data and live subscribe_quote callbacks.
It never imports xttrader and never places orders.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd
from xtquant import xtdata

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(PROJECT_ROOT, "runtime")
os.makedirs(RUNTIME, exist_ok=True)
REPORT_PATH = os.path.join(RUNTIME, "qmt_capability_report.json")

PERIODS = (
    "l2quote",
    "l2quoteaux",
    "l2order",
    "l2transaction",
    "l2transactioncount",
    "l2orderqueue",
)


def _frame_summary(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, pd.DataFrame):
        return {
            "rows": int(len(obj)),
            "columns": [str(c) for c in obj.columns.tolist()],
            "last_index": str(obj.index[-1]) if len(obj) else None,
        }
    return {"type": type(obj).__name__, "rows": 0, "columns": []}


def _callback_count(datas: Any, symbol: str) -> int:
    payload = datas.get(symbol) if isinstance(datas, dict) and symbol in datas else datas
    if isinstance(payload, dict):
        return 1
    if isinstance(payload, (list, tuple)):
        return len(payload)
    return 0


def probe(symbol: str, wait_seconds: float = 6.0) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "symbol": symbol,
        "tested_at": datetime.now().isoformat(timespec="seconds"),
        "wait_seconds": wait_seconds,
        "xtdata_connected": False,
        "periods": {},
    }

    try:
        tick = xtdata.get_full_tick([symbol]) or {}
        result["xtdata_connected"] = symbol in tick and bool(tick.get(symbol))
        if tick.get(symbol):
            row = tick[symbol]
            result["snapshot"] = {
                "lastPrice": row.get("lastPrice"),
                "time": row.get("time"),
                "bid_levels": len(row.get("bidPrice") or []),
                "ask_levels": len(row.get("askPrice") or []),
            }
    except Exception as exc:
        result["connection_error"] = repr(exc)
        return result

    lock = threading.Lock()
    callback_hits: Dict[str, int] = {p: 0 for p in PERIODS}
    subscription_ids: Dict[str, int] = {}

    for period in PERIODS:
        entry: Dict[str, Any] = {
            "available": False,
            "cached_rows": 0,
            "callback_rows": 0,
            "subscription_ok": False,
            "columns": [],
            "errors": [],
        }
        try:
            data = xtdata.get_market_data_ex(
                field_list=[], stock_list=[symbol], period=period,
                start_time="", end_time="", count=50,
                dividend_type="none", fill_data=False,
            ) or {}
            frame = data.get(symbol)
            summary = _frame_summary(frame)
            entry["cached_rows"] = int(summary.get("rows", 0) or 0)
            entry["columns"] = summary.get("columns", [])
            if isinstance(frame, pd.DataFrame) and len(frame):
                entry["sample"] = {
                    str(k): (None if pd.isna(v) else str(v))
                    for k, v in frame.iloc[-1].to_dict().items()
                }
        except Exception as exc:
            entry["errors"].append("cached: " + repr(exc))

        def make_callback(p):
            def callback(datas):
                try:
                    count = _callback_count(datas, symbol)
                    if count:
                        with lock:
                            callback_hits[p] += count
                except Exception:
                    pass
            return callback

        try:
            seq = xtdata.subscribe_quote(symbol, period=period, count=0, callback=make_callback(period))
            entry["subscription_id"] = seq
            entry["subscription_ok"] = isinstance(seq, int) and seq > 0
            if entry["subscription_ok"]:
                subscription_ids[period] = seq
        except Exception as exc:
            entry["errors"].append("subscribe: " + repr(exc))
        result["periods"][period] = entry

    deadline = time.time() + max(0.0, wait_seconds)
    while time.time() < deadline:
        time.sleep(0.2)

    for period, entry in result["periods"].items():
        entry["callback_rows"] = callback_hits.get(period, 0)
        entry["available"] = bool(entry["cached_rows"] > 0 or entry["callback_rows"] > 0)
        if entry["available"]:
            entry["status"] = "OK"
        elif entry["subscription_ok"]:
            entry["status"] = "SUBSCRIBED_NO_DATA"
        else:
            entry["status"] = "UNAVAILABLE"

    for seq in subscription_ids.values():
        try:
            xtdata.unsubscribe_quote(seq)
        except Exception:
            pass

    return result


def main() -> int:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "000400.SZ").strip().upper()
    wait_seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
    report = probe(symbol, wait_seconds)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n=== QMT LEVEL-2 CAPABILITY REPORT ===")
    print(f"Symbol: {symbol}")
    print(f"XtData connected: {report.get('xtdata_connected')}")
    print("Run this during continuous trading for the most reliable result.\n")
    for period, item in report.get("periods", {}).items():
        print(
            f"{period:20s} {item.get('status', 'UNKNOWN'):20s} "
            f"cached={item.get('cached_rows', 0):4d} live={item.get('callback_rows', 0):4d}"
        )
        if item.get("errors"):
            for err in item["errors"]:
                print(f"  {err}")
    print(f"\nReport: {REPORT_PATH}")
    return 0 if report.get("xtdata_connected") else 2


if __name__ == "__main__":
    raise SystemExit(main())
