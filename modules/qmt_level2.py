# -*- coding: utf-8 -*-
"""XtQuant Level-2 subscription manager.

Read-only. On the ROG it subscribes to documented QMT/XtData Level-2 periods.
On Streamlit Cloud, where xtquant is normally unavailable, this module remains
importable so the app can use the Supabase cloud bridge instead of crashing.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any, Dict, Iterable, List, Mapping

import pandas as pd

try:
    from xtquant import xtdata
    XTQUANT_OK = True
    XTQUANT_ERROR = ""
except Exception as exc:  # Streamlit Cloud / non-QMT environments
    xtdata = None
    XTQUANT_OK = False
    XTQUANT_ERROR = str(exc)

from modules.level2_engine import analyze_level2

L2_PERIODS = (
    "l2quote",
    "l2transaction",
    "l2order",
    "l2quoteaux",
    "l2transactioncount",
    "l2orderqueue",
)


def _clean(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
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


def _frame_records(frame: Any) -> List[Dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    out: List[Dict[str, Any]] = []
    for idx, row in frame.iterrows():
        item = _clean(row.to_dict())
        if not item.get("time"):
            item["time"] = str(idx)
        out.append(item)
    return out


def _callback_records(datas: Any, symbol: str) -> List[Dict[str, Any]]:
    payload = datas
    if isinstance(datas, dict) and symbol in datas:
        payload = datas.get(symbol)
    if isinstance(payload, dict):
        return [_clean(payload)]
    if isinstance(payload, (list, tuple)):
        return [_clean(x) for x in payload if isinstance(x, dict)]
    return []


class QMTLevel2Manager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.symbol = ""
        self._sub_ids: Dict[str, int] = {}
        default_error = "" if XTQUANT_OK else f"xtquant unavailable: {XTQUANT_ERROR}"
        self.capabilities: Dict[str, Dict[str, Any]] = {
            p: {"available": False, "subscription_id": None, "error": default_error}
            for p in L2_PERIODS
        }
        self.buffers: Dict[str, deque] = {
            "l2quote": deque(maxlen=1200),
            "l2transaction": deque(maxlen=6000),
            "l2order": deque(maxlen=6000),
            "l2quoteaux": deque(maxlen=1200),
            "l2transactioncount": deque(maxlen=1200),
            "l2orderqueue": deque(maxlen=1200),
        }

    @property
    def available_runtime(self) -> bool:
        return bool(XTQUANT_OK and xtdata is not None)

    def _append(self, period: str, rows: Iterable[Mapping[str, Any]]) -> None:
        with self._lock:
            buf = self.buffers[period]
            for row in rows:
                item = _clean(dict(row))
                if not item:
                    continue
                if buf:
                    prev = buf[-1]
                    key_name = (
                        "tradeIndex" if period == "l2transaction" else
                        "entrustNo" if period == "l2order" else "time"
                    )
                    if item.get(key_name) is not None and item.get(key_name) == prev.get(key_name):
                        continue
                buf.append(item)

    def _make_callback(self, period: str, symbol: str):
        def callback(datas):
            try:
                if symbol != self.symbol:
                    return
                rows = _callback_records(datas, symbol)
                if rows:
                    self._append(period, rows)
                    with self._lock:
                        self.capabilities[period]["available"] = True
                        self.capabilities[period]["error"] = ""
            except Exception as exc:
                with self._lock:
                    self.capabilities[period]["error"] = str(exc)
        return callback

    def stop(self) -> None:
        with self._lock:
            ids = list(self._sub_ids.values())
            self._sub_ids.clear()
        if not self.available_runtime:
            return
        for seq in ids:
            try:
                xtdata.unsubscribe_quote(seq)
            except Exception:
                pass

    def switch(self, symbol: str) -> Dict[str, Any]:
        symbol = str(symbol).upper().strip()
        if not symbol:
            return self.status()
        if symbol == self.symbol:
            return self.status()

        self.stop()
        with self._lock:
            self.symbol = symbol
            for buf in self.buffers.values():
                buf.clear()
            unavailable_error = "" if self.available_runtime else f"xtquant unavailable: {XTQUANT_ERROR}"
            self.capabilities = {
                p: {"available": False, "subscription_id": None, "error": unavailable_error}
                for p in L2_PERIODS
            }

        if not self.available_runtime:
            return self.status()

        for period in L2_PERIODS:
            try:
                result = xtdata.get_market_data_ex(
                    field_list=[], stock_list=[symbol], period=period,
                    start_time="", end_time="", count=500,
                    dividend_type="none", fill_data=False,
                ) or {}
                rows = _frame_records(result.get(symbol))
                if rows:
                    self._append(period, rows)
                    with self._lock:
                        self.capabilities[period]["available"] = True
                        self.capabilities[period]["error"] = ""
            except Exception as exc:
                with self._lock:
                    self.capabilities[period]["error"] = f"backfill: {exc}"

        # XtData documents count=0 as the normal live-only subscription mode.
        for period in L2_PERIODS:
            try:
                seq = xtdata.subscribe_quote(
                    symbol, period=period, count=0,
                    callback=self._make_callback(period, symbol),
                )
                with self._lock:
                    self.capabilities[period]["subscription_id"] = seq
                    if isinstance(seq, int) and seq > 0:
                        self._sub_ids[period] = seq
                    elif not self.capabilities[period]["available"]:
                        self.capabilities[period]["error"] = f"subscribe returned {seq}"
            except Exception as exc:
                with self._lock:
                    if not self.capabilities[period]["available"]:
                        self.capabilities[period]["error"] = f"subscribe: {exc}"

        time.sleep(0.25)
        return self.status()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "symbol": self.symbol,
                "runtime_available": self.available_runtime,
                "runtime_error": "" if self.available_runtime else XTQUANT_ERROR,
                "capabilities": _clean(self.capabilities),
                "counts": {k: len(v) for k, v in self.buffers.items()},
                "available_count": sum(1 for x in self.capabilities.values() if x.get("available")),
            }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            data = {k: list(v) for k, v in self.buffers.items()}
            capabilities = _clean(self.capabilities)
        summary = analyze_level2(
            quotes=data["l2quote"],
            transactions=data["l2transaction"],
            orders=data["l2order"],
            quoteaux=data["l2quoteaux"],
            transactioncount=data["l2transactioncount"],
            orderqueue=data["l2orderqueue"],
            window_seconds=60,
        )
        return {
            "symbol": self.symbol,
            "runtime_available": self.available_runtime,
            "summary": summary,
            "capabilities": capabilities,
            "recent_transactions": data["l2transaction"][-120:],
            "recent_orders": data["l2order"][-120:],
            "quoteaux": data["l2quoteaux"][-1] if data["l2quoteaux"] else {},
            "orderqueue": data["l2orderqueue"][-1] if data["l2orderqueue"] else {},
        }
