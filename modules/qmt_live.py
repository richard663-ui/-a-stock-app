# -*- coding: utf-8 -*-
"""QMT本地实时管理器。

由Streamlit页面在后台启动一个常驻线程；用户在页面更换股票代码时，
采集器自动切换到对应股票，不需要再打开单独PowerShell。
只读行情，不导入xttrader，不下单。
"""
from __future__ import annotations

import csv
import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict

import pandas as pd

try:
    from xtquant import xtdata
    XTQUANT_OK = True
    XTQUANT_ERROR = ""
except Exception as exc:  # pragma: no cover - 仅在本机环境判断
    xtdata = None
    XTQUANT_OK = False
    XTQUANT_ERROR = str(exc)


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


class QMTLiveManager:
    def __init__(self, interval: float = 1.0, max_rows: int = 1800, runtime_dir: str = "runtime"):
        self.interval = max(0.3, float(interval))
        self.max_rows = max(300, int(max_rows))
        self.runtime_dir = runtime_dir
        os.makedirs(runtime_dir, exist_ok=True)

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._symbol = ""
        self._rows = deque(maxlen=self.max_rows)
        self._status = "等待输入股票代码"
        self._error = "" if XTQUANT_OK else XTQUANT_ERROR
        self._last_write_symbol = ""

        self._thread = threading.Thread(target=self._run, name="qmt-live-manager", daemon=True)
        self._thread.start()

    def set_symbol(self, symbol: str) -> None:
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return
        with self._lock:
            if symbol == self._symbol:
                return
            self._symbol = symbol
            self._rows.clear()
            self._status = f"正在切换到 {symbol}"
            self._error = ""
            self._last_write_symbol = ""

    def get_frame(self) -> pd.DataFrame:
        with self._lock:
            return pd.DataFrame(list(self._rows))

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            latest = self._rows[-1] if self._rows else {}
            return {
                "ok": bool(self._rows),
                "symbol": self._symbol,
                "status": self._status,
                "error": self._error,
                "samples": len(self._rows),
                "captured_at": latest.get("captured_at"),
            }

    def stop(self) -> None:
        self._stop.set()

    def _append_csv(self, row: dict) -> None:
        symbol = row["symbol"].replace(".", "_")
        path = os.path.join(self.runtime_dir, f"qmt_ticks_{symbol}.csv")
        write_header = not os.path.exists(path) or self._last_write_symbol != row["symbol"]
        with open(path, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if write_header and os.path.getsize(path) == 0:
                writer.writeheader()
            writer.writerow(row)
        latest_path = os.path.join(self.runtime_dir, "qmt_latest.json")
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(row, f, ensure_ascii=False)
        self._last_write_symbol = row["symbol"]

    def _run(self) -> None:
        if not XTQUANT_OK:
            with self._lock:
                self._status = "xtquant未安装"
            return

        while not self._stop.is_set():
            with self._lock:
                symbol = self._symbol
            if not symbol:
                time.sleep(0.2)
                continue

            try:
                data = xtdata.get_full_tick([symbol]) or {}
                tick = data.get(symbol) or {}
                if not tick:
                    with self._lock:
                        if symbol == self._symbol:
                            self._status = f"{symbol} 暂无行情"
                    time.sleep(self.interval)
                    continue

                row = normalize_tick(symbol, tick)
                with self._lock:
                    if symbol != self._symbol:
                        continue
                    self._rows.append(row)
                    self._status = "国盛QMT实时连接"
                    self._error = ""
                try:
                    self._append_csv(row)
                except Exception:
                    pass
            except Exception as exc:
                with self._lock:
                    if symbol == self._symbol:
                        self._status = "QMT连接失败"
                        self._error = str(exc)
            time.sleep(self.interval)
