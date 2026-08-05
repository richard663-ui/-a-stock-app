# -*- coding: utf-8 -*-
"""本地QMT实时采集器：每秒保存一次快照，供Streamlit读取。
只读行情，不导入xttrader，不下单。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import datetime

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
        "avgPrice": tick.get("avgPrice"),
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
    p.add_argument("--out", default="runtime/qmt_ticks.csv")
    args = p.parse_args()

    symbol = args.symbol.upper().strip()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    # 每次启动都清空旧文件，避免把不同股票混在一起。
    if os.path.exists(args.out):
        os.remove(args.out)
    latest_path = args.out.replace(".csv", "_latest.json")
    if os.path.exists(latest_path):
        os.remove(latest_path)

    print(f"QMT collector started: {symbol} -> {args.out}")
    print("Read-only mode. Press Ctrl+C to stop.")

    fieldnames = list(normalize_tick(symbol, {}).keys())
    while True:
        try:
            data = xtdata.get_full_tick([symbol]) or {}
            tick = data.get(symbol) or {}
            if tick:
                row = normalize_tick(symbol, tick)
                write_header = not os.path.exists(args.out)
                with open(args.out, "a", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    if write_header:
                        writer.writeheader()
                    writer.writerow(row)
                with open(latest_path, "w", encoding="utf-8") as f:
                    json.dump(row, f, ensure_ascii=False)
                print(
                    f"{row['captured_at']} {symbol} price={row['lastPrice']} "
                    f"day={row['lastClose']}->{row['lastPrice']}"
                )
            else:
                print(f"No tick returned for {symbol}")
        except KeyboardInterrupt:
            print("Collector stopped")
            break
        except Exception as e:
            print(f"collector error: {e}")
        time.sleep(max(0.2, args.interval))


if __name__ == "__main__":
    main()
