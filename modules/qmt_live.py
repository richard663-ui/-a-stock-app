# -*- coding: utf-8 -*-
"""Local QMT real-time manager used when Streamlit runs on the ROG."""
from __future__ import annotations

import csv
import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, Iterable, List

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
    if isinstance(value, (datetime, pd.Timestamp)):
        return pd.Timestamp(value).to_pydatetime().isoformat(timespec="milliseconds")
    text = str(value or "").strip()
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


def normalize_tick(symbol: str, tick: dict, fallback_time: Any = None) -> dict:
    captured = tick.get("captured_at") or tick.get("time") or tick.get("timetag") or fallback_time
    return {
        "symbol": symbol,
        "captured_at": _iso_time(captured),
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


def _records_from_frame(symbol: str, frame: pd.DataFrame) -> Iterable[dict]:
    if frame is None or frame.empty:
        return []
    out: List[dict] = []
    seen = set()
    for idx, row in frame.tail(300).iterrows():
        item = normalize_tick(symbol, row.to_dict(), fallback_time=idx)
        key = (item.get("captured_at"), item.get("lastPrice"), item.get("volume"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    out.sort(key=lambda x: str(x.get("captured_at") or ""))
    return out


class QMTLiveManager:
    def __init__(self, interval: float = 1.0, max_rows: int = 1800, runtime_dir: str = "runtime"):
        self.interval = max(0.25, float(interval))
        self.max_rows = max(300, int(max_rows))
        self.runtime_dir = runtime_dir
        os.makedirs(runtime_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._symbol = ""
        self._rows = deque(maxlen=self.max_rows)
        self._status = "等待输入股票代码"
        self._error = "" if XTQUANT_OK else XTQUANT_ERROR
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
            self._status = f"正在切换 {symbol}"
            self._error = ""

    def get_frame(self) -> pd.DataFrame:
        with self._lock:
            return pd.DataFrame(list(self._rows))

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            latest = self._rows[-1] if self._rows else {}
            return {
                "ok": bool(self._rows), "symbol": self._symbol,
                "status": self._status, "error": self._error,
                "samples": len(self._rows), "captured_at": latest.get("captured_at"),
            }

    def stop(self) -> None:
        self._stop.set()

    def _append_unique(self, row: dict) -> None:
        if not row:
            return
        with self._lock:
            if not self._rows:
                self._rows.append(row)
                return
            latest = self._rows[-1]
            if (
                row.get("captured_at") != latest.get("captured_at")
                or row.get("lastPrice") != latest.get("lastPrice")
                or row.get("volume") != latest.get("volume")
            ):
                self._rows.append(row)

    def _append_csv(self, row: dict) -> None:
        symbol = row["symbol"].replace(".", "_")
        path = os.path.join(self.runtime_dir, f"qmt_ticks_{symbol}.csv")
        with open(path, "a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
            if os.path.getsize(path) == 0:
                writer.writeheader()
            writer.writerow(row)
        with open(os.path.join(self.runtime_dir, "qmt_latest.json"), "w", encoding="utf-8") as handle:
            json.dump(row, handle, ensure_ascii=False)

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
        time.sleep(0.6)
        try:
            result = xtdata.get_market_data_ex(
                field_list=[], stock_list=[symbol], period="tick",
                start_time="", end_time="", count=300,
                dividend_type="none", fill_data=False,
            ) or {}
            records = list(_records_from_frame(symbol, result.get(symbol)))
            with self._lock:
                if symbol == self._symbol:
                    self._rows.clear()
                    self._rows.extend(records)
                    self._status = f"已回填 {len(records)} 条，继续实时更新"
        except Exception as exc:
            with self._lock:
                if symbol == self._symbol:
                    self._status = "回填失败，正在实时积累"
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
                if symbol == self._symbol:
                    self._append_unique(row)
                    with self._lock:
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
