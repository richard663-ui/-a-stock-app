# -*- coding: utf-8 -*-
"""QMT local real-time manager used when Streamlit runs on the ROG.

The manager automatically switches symbols, backfills today's recent tick data,
and then keeps collecting snapshots. It is read-only and never imports xttrader.
"""
from __future__ import annotations

import csv
import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, Iterable

import pandas as pd

try:
    from xtquant import xtdata
    XTQUANT_OK = True
    XTQUANT_ERROR = ""
except Exception as exc:  # pragma: no cover
    xtdata = None
    XTQUANT_OK = False
    XTQUANT_ERROR = str(exc)


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


def normalize_tick(symbol: str, tick: dict) -> dict:
    return {
        "symbol": symbol,
        "captured_at": tick.get("captured_at") or _iso_time(tick.get("time")),
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


def _records_from_frame(symbol: str, frame: pd.DataFrame) -> Iterable[dict]:
    if frame is None or frame.empty:
        return []
    out = []
    for idx, row in frame.tail(300).iterrows():
        raw = row.to_dict()
        raw.setdefault("timetag", str(idx))
        out.append(normalize_tick(symbol, raw))
    return out


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
        self._subscription_id = None

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
            self._status = f"正在加载 {symbol} 完整实时数据"
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
        with open(path, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if os.path.getsize(path) == 0:
                writer.writeheader()
            writer.writerow(row)
        latest_path = os.path.join(self.runtime_dir, "qmt_latest.json")
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(row, f, ensure_ascii=False)

    def _switch_subscription(self, symbol: str) -> None:
        if self._subscription_id is not None:
            try:
                xtdata.unsubscribe_quote(self._subscription_id)
            except Exception:
                pass
        try:
            self._subscription_id = xtdata.subscribe_quote(symbol, period="tick", count=-1)
        except Exception:
            self._subscription_id = None

        time.sleep(0.7)
        try:
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
            records = list(_records_from_frame(symbol, result.get(symbol)))
            with self._lock:
                if symbol == self._symbol:
                    self._rows.extend(records)
                    self._status = f"已加载 {len(records)} 条历史分笔，继续实时更新"
        except Exception as exc:
            with self._lock:
                if symbol == self._symbol:
                    self._status = "历史分笔未加载，正在实时积累"
                    self._error = str(exc)

    def _run(self) -> None:
        if not XTQUANT_OK:
            with self._lock:
                self._status = "xtquant未安装"
            return

        active_symbol = ""
        while not self._stop.is_set():
            with self._lock:
                symbol = self._symbol
            if not symbol:
                time.sleep(0.2)
                continue

            if symbol != active_symbol:
                active_symbol = symbol
                self._switch_subscription(symbol)

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
                    if not self._rows or row.get("captured_at") != self._rows[-1].get("captured_at") or row.get("lastPrice") != self._rows[-1].get("lastPrice"):
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
