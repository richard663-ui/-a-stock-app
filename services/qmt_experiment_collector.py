# -*- coding: utf-8 -*-
"""本地QMT实时采集器：每秒保存一次快照，供Streamlit读取。
只读行情，不导入xttrader，不下单。
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime

import pandas as pd
from xtquant import xtdata


def _first(values, default=None):
    return values[0] if isinstance(values, (list, tuple)) and values else default


def normalize_tick(symbol: str, tick: dict) -> dict:
    return {
        "symbol": symbol,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "time": tick.get("time"),
        "timetag": tick.get("timetag"),
        "lastPrice": tick.get("lastPrice"),
        "open": tick.get("open"),
        "high": tick.get("high"),
        "low": tick.get("low"),
        "lastClose": tick.get("lastClose"),
        "amount": tick.get("amount"),
        "volume": tick.get("volume"),
        "bidPrice": tick.get("bidPrice") or [],
        "askPrice": tick.get("askPrice") or [],
        "bidVol": tick.get("bidVol") or [],
        "askVol": tick.get("askVol") or [],
        "bidPrice1": _first(tick.get("bidPrice")),
        "askPrice1": _first(tick.get("askPrice")),
        "bidVol1": _first(tick.get("bidVol")),
        "askVol1": _first(tick.get("askVol")),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="000400.SZ")
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--max-rows", type=int, default=1200)
    p.add_argument("--out", default="runtime/qmt_ticks.csv")
    args = p.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    print(f"QMT collector started: {args.symbol} -> {args.out}")
    print("Read-only mode. Press Ctrl+C to stop.")

    rows = []
    while True:
        try:
            data = xtdata.get_full_tick([args.symbol]) or {}
            tick = data.get(args.symbol) or {}
            if tick:
                row = normalize_tick(args.symbol, tick)
                rows.append(row)
                rows = rows[-args.max_rows:]
                pd.DataFrame(rows).to_csv(args.out, index=False, encoding="utf-8-sig")
                with open(args.out.replace(".csv", "_latest.json"), "w", encoding="utf-8") as f:
                    json.dump(row, f, ensure_ascii=False)
                print(f"{row['captured_at']} price={row['lastPrice']} volume={row['volume']}")
            else:
                print("No tick returned")
        except KeyboardInterrupt:
            print("Collector stopped")
            break
        except Exception as e:
            print(f"collector error: {e}")
        time.sleep(max(0.2, args.interval))


if __name__ == "__main__":
    main()
