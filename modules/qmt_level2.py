# -*- coding: utf-8 -*-
"""XtQuant Level-2 subscription manager.

Read-only. The manager uses two independent paths on QMT:
1) subscribe_quote keeps the broker-side Level-2 cache fed;
2) dedicated get_l2_* polling drains that cache even when callback payloads are
   ndarray/DataFrame or callbacks are not dispatched by a broker build.

On Streamlit Cloud, where xtquant is normally unavailable, this module remains
importable so the app can use the Supabase cloud bridge instead of crashing.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

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
CORE_PERIODS = ("l2transaction", "l2order", "l2quote")
CORE_POLL_SECONDS = 0.75
AUX_POLL_SECONDS = 3.0
CACHE_COUNT = 500


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
        if isinstance(item, dict) and not item.get("time"):
            item["time"] = _clean(idx)
        if isinstance(item, dict) and item:
            out.append(item)
    return out


def _array_records(payload: Any) -> List[Dict[str, Any]]:
    """Convert numpy structured arrays without importing numpy directly."""
    dtype = getattr(payload, "dtype", None)
    names = getattr(dtype, "names", None)
    if not names:
        return []
    out: List[Dict[str, Any]] = []
    try:
        for row in payload:
            item = {str(name): _clean(row[name]) for name in names}
            if item:
                out.append(item)
    except Exception:
        return []
    return out


def _records_any(payload: Any, symbol: str = "") -> List[Dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, dict) and symbol and symbol in payload:
        payload = payload.get(symbol)
    if isinstance(payload, pd.DataFrame):
        return _frame_records(payload)
    arr = _array_records(payload)
    if arr:
        return arr
    if isinstance(payload, Mapping):
        # Some broker builds return {field: scalar/list}; only scalar dicts are
        # treated as one row. Nested symbol dicts are handled above.
        cleaned = _clean(dict(payload))
        return [cleaned] if isinstance(cleaned, dict) and cleaned else []
    if isinstance(payload, (list, tuple)):
        out: List[Dict[str, Any]] = []
        for x in payload:
            if isinstance(x, Mapping):
                item = _clean(dict(x))
                if isinstance(item, dict) and item:
                    out.append(item)
            else:
                out.extend(_array_records(x))
        return out
    return []


def _row_key(period: str, row: Mapping[str, Any]) -> Tuple[Any, ...]:
    if period == "l2transaction":
        idx = row.get("tradeIndex")
        if idx not in (None, ""):
            return (period, idx)
        return (period, row.get("time"), row.get("buyNo"), row.get("sellNo"), row.get("price"), row.get("volume"))
    if period == "l2order":
        no = row.get("entrustNo")
        if no not in (None, ""):
            return (period, no)
        return (period, row.get("time"), row.get("price"), row.get("volume"), row.get("entrustDirection"))
    return (period, row.get("time"), row.get("lastPrice"), row.get("transactionNum"), row.get("volume"))


class QMTLevel2Manager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.symbol = ""
        self._sub_ids: Dict[str, int] = {}
        self._last_core_poll = 0.0
        self._last_aux_poll = 0.0
        self._seen: Dict[str, set] = {p: set() for p in L2_PERIODS}
        self._seen_order: Dict[str, deque] = {p: deque(maxlen=12000) for p in L2_PERIODS}
        default_error = "" if XTQUANT_OK else f"xtquant unavailable: {XTQUANT_ERROR}"
        self.capabilities: Dict[str, Dict[str, Any]] = {
            p: {"available": False, "subscription_id": None, "error": default_error, "source": None}
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

    def _remember_key(self, period: str, key: Tuple[Any, ...]) -> bool:
        seen = self._seen[period]
        if key in seen:
            return False
        q = self._seen_order[period]
        if q.maxlen and len(q) >= q.maxlen:
            old = q.popleft()
            seen.discard(old)
        q.append(key)
        seen.add(key)
        return True

    def _append(self, period: str, rows: Iterable[Mapping[str, Any]], source: str = "callback") -> int:
        added = 0
        with self._lock:
            buf = self.buffers[period]
            for row in rows:
                item = _clean(dict(row))
                if not isinstance(item, dict) or not item:
                    continue
                key = _row_key(period, item)
                if not self._remember_key(period, key):
                    continue
                buf.append(item)
                added += 1
            if added:
                self.capabilities[period]["available"] = True
                self.capabilities[period]["error"] = ""
                self.capabilities[period]["source"] = source
        return added

    def _make_callback(self, period: str, symbol: str):
        def callback(datas):
            try:
                if symbol != self.symbol:
                    return
                rows = _records_any(datas, symbol)
                if rows:
                    self._append(period, rows, source="callback")
            except Exception as exc:
                with self._lock:
                    self.capabilities[period]["error"] = f"callback: {exc}"
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

    def _dedicated_getter(self, period: str):
        if not self.available_runtime:
            return None
        name = {
            "l2quote": "get_l2_quote",
            "l2order": "get_l2_order",
            "l2transaction": "get_l2_transaction",
        }.get(period)
        return getattr(xtdata, name, None) if name else None

    def _poll_one(self, period: str) -> int:
        if not self.available_runtime or not self.symbol:
            return 0
        try:
            getter = self._dedicated_getter(period)
            if callable(getter):
                payload = getter(field_list=[], stock_code=self.symbol, start_time="", end_time="", count=CACHE_COUNT)
                rows = _records_any(payload, self.symbol)
                return self._append(period, rows, source="get_l2_cache") if rows else 0

            # Optional L2 periods do not have stable dedicated getters in every
            # XtQuant build. Reading the cache through get_market_data_ex is a
            # fallback, not the primary path for core transaction/order data.
            result = xtdata.get_market_data_ex(
                field_list=[], stock_list=[self.symbol], period=period,
                start_time="", end_time="", count=CACHE_COUNT,
                dividend_type="none", fill_data=False,
            ) or {}
            rows = _records_any(result, self.symbol)
            return self._append(period, rows, source="market_data_ex_cache") if rows else 0
        except Exception as exc:
            with self._lock:
                previous = str(self.capabilities[period].get("error") or "")
                text = f"cache_poll: {exc}"
                if text != previous:
                    self.capabilities[period]["error"] = text
            return 0

    def refresh(self, force: bool = False) -> Dict[str, int]:
        """Drain broker-side L2 caches into local buffers.

        Core transaction/order/quote caches are polled frequently. Optional
        quoteaux/orderqueue/transactioncount are intentionally slower to avoid
        creating another CPU/IPC spike across the eight-stock basket.
        """
        if not self.available_runtime or not self.symbol:
            return {}
        now = time.time()
        out: Dict[str, int] = {}
        if force or now - self._last_core_poll >= CORE_POLL_SECONDS:
            for period in CORE_PERIODS:
                out[period] = self._poll_one(period)
            self._last_core_poll = now
        if force or now - self._last_aux_poll >= AUX_POLL_SECONDS:
            for period in ("l2quoteaux", "l2transactioncount", "l2orderqueue"):
                out[period] = self._poll_one(period)
            self._last_aux_poll = now
        return out

    def switch(self, symbol: str) -> Dict[str, Any]:
        symbol = str(symbol).upper().strip()
        if not symbol:
            return self.status()
        if symbol == self.symbol:
            self.refresh(force=True)
            return self.status()

        self.stop()
        with self._lock:
            self.symbol = symbol
            self._last_core_poll = 0.0
            self._last_aux_poll = 0.0
            for buf in self.buffers.values():
                buf.clear()
            self._seen = {p: set() for p in L2_PERIODS}
            self._seen_order = {p: deque(maxlen=12000) for p in L2_PERIODS}
            unavailable_error = "" if self.available_runtime else f"xtquant unavailable: {XTQUANT_ERROR}"
            self.capabilities = {
                p: {"available": False, "subscription_id": None, "error": unavailable_error, "source": None}
                for p in L2_PERIODS
            }

        if not self.available_runtime:
            return self.status()

        # Subscribe first. Dedicated get_l2_* calls below read the cache filled by
        # these subscriptions. This matches XtData's L2 runtime model.
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
                    else:
                        self.capabilities[period]["error"] = f"subscribe returned {seq}"
            except Exception as exc:
                with self._lock:
                    self.capabilities[period]["error"] = f"subscribe: {exc}"

        # Give MiniQMT a brief moment to seed its cache, then drain immediately.
        time.sleep(0.15)
        self.refresh(force=True)
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
                "core_ready": bool(
                    self.capabilities.get("l2transaction", {}).get("available") and
                    self.capabilities.get("l2order", {}).get("available")
                ),
            }

    def snapshot(self) -> Dict[str, Any]:
        self.refresh()
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
            "counts": {k: len(v) for k, v in data.items()},
            "recent_transactions": data["l2transaction"][-120:],
            "recent_orders": data["l2order"][-120:],
            "quoteaux": data["l2quoteaux"][-1] if data["l2quoteaux"] else {},
            "orderqueue": data["l2orderqueue"][-1] if data["l2orderqueue"] else {},
        }
