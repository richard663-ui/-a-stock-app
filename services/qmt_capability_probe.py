# -*- coding: utf-8 -*-
"""Probe Guosheng QMT/XtQuant market-data capabilities without placing orders.

Usage:
    py services/qmt_capability_probe.py 000400.SZ

It checks tick plus documented Level-2 periods and writes a JSON report to
runtime/qmt_capability_report.json. Read-only: xttrader is never imported.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict

import pandas as pd
from xtquant import xtdata

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(PROJECT_ROOT, "runtime")
os.makedirs(RUNTIME, exist_ok=True)
REPORT_PATH = os.path.join(RUNTIME, "qmt_capability_report.json")

PERIODS = (
    "tick",
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


def probe(symbol: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "symbol": symbol,
        "tested_at": datetime.now().isoformat(timespec="seconds"),
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

    for period in PERIODS:
        entry: Dict[str, Any] = {"ok": False, "rows": 0, "columns": []}
        try:
            data = xtdata.get_market_data_ex(
                field_list=[],
                stock_list=[symbol],
                period=period,
                start_time="",
                end_time="",
                count=20,
                dividend_type="none",
                fill_data=False,
            ) or {}
            frame = data.get(symbol)
            summary = _frame_summary(frame)
            entry.update(summary)
            entry["ok"] = bool(summary.get("rows", 0) > 0)
            if isinstance(frame, pd.DataFrame) and len(frame):
                entry["sample"] = {
                    str(k): (None if pd.isna(v) else str(v))
                    for k, v in frame.iloc[-1].to_dict().items()
                }
        except Exception as exc:
            entry["error"] = repr(exc)
        result["periods"][period] = entry

    return result


def main() -> int:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "000400.SZ").strip().upper()
    report = probe(symbol)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n=== QMT CAPABILITY REPORT ===")
    print(f"Symbol: {symbol}")
    print(f"XtData connected: {report.get('xtdata_connected')}")
    for period, item in report.get("periods", {}).items():
        status = "OK" if item.get("ok") else "NO DATA"
        print(f"{period:20s} {status:8s} rows={item.get('rows', 0)}")
        if item.get("error"):
            print(f"  error: {item['error']}")
    print(f"Report: {REPORT_PATH}")
    return 0 if report.get("xtdata_connected") else 2


if __name__ == "__main__":
    raise SystemExit(main())
