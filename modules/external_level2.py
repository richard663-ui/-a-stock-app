# -*- coding: utf-8 -*-
"""External Level-2 adapter for Level2API/L2-push-python.

Research/data only. This module does not place orders and never stores provider
credentials in the repository. The provider's local txtool proxy owns remote
credentials; this adapter only connects to that local gRPC endpoint.

The public interface intentionally mirrors QMTLevel2Manager so the existing
5-second sample / +60s label / ML pipeline can switch data providers without a
model rewrite.
"""
from __future__ import annotations

import importlib
import math
import os
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Tuple

from modules.level2_engine import analyze_level2

try:
    import tomllib
except Exception:  # pragma: no cover
    tomllib = None

try:
    from xtquant import xtdata as _xtdata
except Exception:  # non-QMT environments
    _xtdata = None

PERIODS = (
    "l2quote",
    "l2transaction",
    "l2order",
    "l2quoteaux",
    "l2transactioncount",
    "l2orderqueue",
)
CONFIG_PATH = Path.home() / ".a_stock_qmt" / "external_l2.toml"
DEFAULT_CLIENT_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AStockQMT" / "vendor" / "level2api"


def _clean(v: Any) -> Any:
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    try:
        return _clean(v.item())
    except Exception:
        return v


def _cfg() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "mode": "qmt",
        "address": "localhost:5000",
        "client_dir": str(DEFAULT_CLIENT_DIR),
        "auto_subscribe": True,
        "topic_mask": 15,
        "price_divisor": 0.0,
    }
    if tomllib is None or not CONFIG_PATH.exists():
        return out
    try:
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        provider = dict(data.get("provider") or {})
        out.update(provider)
    except Exception:
        pass
    return out


def external_level2_enabled() -> bool:
    return str(_cfg().get("mode") or "").strip().lower() in {"level2api", "external", "grpc"}


def provider_name() -> str:
    return "level2api" if external_level2_enabled() else "qmt"


def _market_code(symbol: str) -> int:
    market = str(symbol).upper().split(".")[-1] if "." in str(symbol) else ""
    return 1 if market == "SH" else 2 if market == "SZ" else 3 if market == "BJ" else 0


def _symbol(exchange: int, code: str) -> str:
    suffix = "SH" if int(exchange or 0) == 1 else "SZ" if int(exchange or 0) == 2 else "BJ" if int(exchange or 0) == 3 else ""
    return f"{str(code).zfill(6)}.{suffix}" if suffix else str(code).zfill(6)


def _msg_value(msg: Any, name: str, default: Any = 0) -> Any:
    try:
        return getattr(msg, name)
    except Exception:
        return default


class _Hub:
    """One gRPC connection and four stream consumers shared by all 8 symbols."""

    def __init__(self) -> None:
        self.cfg = _cfg()
        self.lock = threading.RLock()
        self.started = False
        self.stop_event = threading.Event()
        self.channel = None
        self.stub = None
        self.entity = None
        self.grpc = None
        self.last_error = ""
        self.subscribed: Dict[str, str] = {}
        self.price_divisors: Dict[str, float] = {}
        self.buffers: Dict[str, Dict[str, Deque[Dict[str, Any]]]] = defaultdict(self._new_buffers)
        self.last_update: Dict[str, Dict[str, float]] = defaultdict(lambda: {p: 0.0 for p in PERIODS})
        self._load_client()

    @staticmethod
    def _new_buffers() -> Dict[str, Deque[Dict[str, Any]]]:
        return {
            "l2quote": deque(maxlen=1200),
            "l2transaction": deque(maxlen=6000),
            "l2order": deque(maxlen=6000),
            "l2quoteaux": deque(maxlen=1200),
            "l2transactioncount": deque(maxlen=1200),
            "l2orderqueue": deque(maxlen=1200),
        }

    def _load_client(self) -> None:
        client_dir = Path(str(self.cfg.get("client_dir") or DEFAULT_CLIENT_DIR)).expanduser()
        if str(client_dir) not in sys.path:
            sys.path.insert(0, str(client_dir))
        try:
            self.grpc = importlib.import_module("grpc")
            self.entity = importlib.import_module("entity_pb2")
            proxy_grpc = importlib.import_module("proxy_pb2_grpc")
            self.channel = self.grpc.insecure_channel(str(self.cfg.get("address") or "localhost:5000"))
            self.stub = proxy_grpc.ProxyStub(self.channel)
            self.last_error = ""
        except Exception as exc:
            self.last_error = f"external client unavailable: {exc}"

    @property
    def runtime_available(self) -> bool:
        return bool(self.stub is not None and self.entity is not None)

    def _qmt_last(self, symbol: str) -> float:
        if _xtdata is None:
            return 0.0
        try:
            raw = (_xtdata.get_full_tick([symbol]) or {}).get(symbol) or {}
            return float(raw.get("lastPrice") or 0.0)
        except Exception:
            return 0.0

    def _price(self, symbol: str, raw: Any) -> float:
        try:
            x = float(raw or 0.0)
        except Exception:
            return 0.0
        if x <= 0:
            return 0.0
        forced = float(self.cfg.get("price_divisor") or 0.0)
        if forced > 0:
            return x / forced
        divisor = self.price_divisors.get(symbol)
        if divisor:
            return x / divisor

        # Prefer automatic calibration against QMT L1. It avoids assuming the
        # commercial provider's integer price scale and does not require a user
        # setting when QMT L1 is available.
        ref = self._qmt_last(symbol)
        candidates = (1.0, 10.0, 100.0, 1000.0, 10000.0)
        if ref > 0:
            divisor = min(candidates, key=lambda d: abs(x / d - ref) / ref)
        else:
            # Common A-share binary feeds encode price in 1/10000 yuan. This is
            # only a fallback; current ML direction ratios do not rely on price
            # scale when amount is present.
            divisor = 10000.0 if x >= 10000 else 1.0
        self.price_divisors[symbol] = divisor
        return x / divisor

    def _append(self, symbol: str, period: str, row: Dict[str, Any]) -> None:
        if not symbol or period not in PERIODS or not row:
            return
        with self.lock:
            self.buffers[symbol][period].append(row)
            self.last_update[symbol][period] = time.time()

    def _on_tick(self, msg: Any) -> None:
        s = _symbol(_msg_value(msg, "stock_exchange"), _msg_value(msg, "stock_code", ""))
        kind = int(_msg_value(msg, "tx_kind", 0) or 0)
        tx_dir = int(_msg_value(msg, "tx_dir", 0) or 0)
        trade_flag = 3 if kind != 0 else tx_dir
        self._append(s, "l2transaction", {
            "time": int(_msg_value(msg, "created_at", 0) or 0),
            "tradeIndex": str(_msg_value(msg, "code", "")),
            "price": self._price(s, _msg_value(msg, "price", 0)),
            "volume": float(_msg_value(msg, "volume", 0) or 0),
            "amount": float(_msg_value(msg, "amount", 0) or 0),
            "tradeFlag": trade_flag,
            "buyNo": str(_msg_value(msg, "buy_order_seq", "")),
            "sellNo": str(_msg_value(msg, "sell_order_seq", "")),
            "providerTxKind": kind,
        })

    def _on_order(self, msg: Any) -> None:
        s = _symbol(_msg_value(msg, "stock_exchange"), _msg_value(msg, "stock_code", ""))
        tx_dir = int(_msg_value(msg, "tx_dir", 0) or 0)
        kind = int(_msg_value(msg, "tx_kind", 0) or 0)
        if kind == 10:
            direction = 3 if tx_dir == 1 else 4 if tx_dir == 2 else 0
        else:
            direction = tx_dir
        self._append(s, "l2order", {
            "time": int(_msg_value(msg, "created_at", 0) or 0),
            "entrustNo": str(_msg_value(msg, "code", "")),
            "price": self._price(s, _msg_value(msg, "price", 0)),
            "volume": float(_msg_value(msg, "volume", 0) or 0),
            "amount": float(_msg_value(msg, "amount", 0) or 0),
            "entrustDirection": direction,
            "providerTxKind": kind,
        })

    def _on_queue(self, msg: Any) -> None:
        s = _symbol(_msg_value(msg, "stock_exchange"), _msg_value(msg, "stock_code", ""))
        self._append(s, "l2orderqueue", {
            "time": int(_msg_value(msg, "created_at", 0) or 0),
            "bidPrice": self._price(s, _msg_value(msg, "bid1_price", 0)),
            "offerPrice": self._price(s, _msg_value(msg, "ask1_price", 0)),
            "bidLevelVolume": [float(x) for x in list(_msg_value(msg, "bid_volume_detail", []) or [])],
            "offerLevelVolume": [float(x) for x in list(_msg_value(msg, "ask_volume_detail", []) or [])],
            "bidQuantity": int(_msg_value(msg, "bid1_quantity", 0) or 0),
            "offerQuantity": int(_msg_value(msg, "ask1_quantity", 0) or 0),
        })

    def _on_quote(self, msg: Any) -> None:
        s = _symbol(_msg_value(msg, "stock_exchange"), _msg_value(msg, "stock_code", ""))
        self._append(s, "l2quote", {
            "time": int(_msg_value(msg, "created_at", 0) or 0),
            "status": int(_msg_value(msg, "status", 0) or 0),
            "lastClose": self._price(s, _msg_value(msg, "prev_close_price", 0)),
            "open": self._price(s, _msg_value(msg, "open_price", 0)),
            "lastPrice": self._price(s, _msg_value(msg, "latest_price", 0)),
            "high": self._price(s, _msg_value(msg, "high_price", 0)),
            "low": self._price(s, _msg_value(msg, "low_price", 0)),
            "volume": float(_msg_value(msg, "volume", 0) or 0),
            "amount": float(_msg_value(msg, "amount", 0) or 0),
            "transactionNum": int(_msg_value(msg, "order_quantity", 0) or 0),
            "bidPrice": [self._price(s, x) for x in list(_msg_value(msg, "bid_price_detail", []) or [])],
            "bidVol": [float(x) for x in list(_msg_value(msg, "bid_volume_detail", []) or [])],
            "askPrice": [self._price(s, x) for x in list(_msg_value(msg, "ask_price_detail", []) or [])],
            "askVol": [float(x) for x in list(_msg_value(msg, "ask_volume_detail", []) or [])],
            "totalBidQuantity": float(_msg_value(msg, "bid_volume", 0) or 0),
            "totalOffQuantity": float(_msg_value(msg, "ask_volume", 0) or 0),
        })

    def _receiver(self, method_name: str, handler) -> None:
        while not self.stop_event.is_set():
            try:
                if not self.runtime_available:
                    self._load_client()
                    if not self.runtime_available:
                        time.sleep(2.0)
                        continue
                stream = getattr(self.stub, method_name)(self.entity.Void())
                self.last_error = ""
                for msg in stream:
                    if self.stop_event.is_set():
                        return
                    handler(msg)
            except Exception as exc:
                self.last_error = f"{method_name}: {exc}"
                time.sleep(1.0)

    def start(self) -> None:
        with self.lock:
            if self.started:
                return
            self.started = True
        if not self.runtime_available:
            return
        streams = (
            ("NewTickRecordStream", self._on_tick),
            ("NewOrderRecordStream", self._on_order),
            ("NewOrderQueueRecordStream", self._on_queue),
            ("NewStockQuoteRecordStream", self._on_quote),
        )
        for method, handler in streams:
            threading.Thread(
                target=self._receiver,
                args=(method, handler),
                daemon=True,
                name=f"astock-level2api-{method}",
            ).start()

    def ensure_subscription(self, symbol: str) -> Tuple[bool, str]:
        self.start()
        symbol = str(symbol).upper().strip()
        if not symbol:
            return False, "empty symbol"
        with self.lock:
            if symbol in self.subscribed:
                return True, self.subscribed[symbol]
        if not self.runtime_available:
            return False, self.last_error or "external client unavailable"
        market = _market_code(symbol)
        code = symbol.split(".")[0]
        if not market:
            return False, f"unsupported symbol {symbol}"
        topic = f"{market}_{code}_{int(self.cfg.get('topic_mask') or 15)}"
        if not bool(self.cfg.get("auto_subscribe", True)):
            with self.lock:
                self.subscribed[symbol] = topic
            return True, topic
        try:
            req = self.entity.String(value=topic)
            result = self.stub.AddSubscription(req, timeout=4.0)
            code_value = int(getattr(result, "code", 0) or 0)
            if code_value not in (0, 1):
                return False, f"AddSubscription code={code_value} result={result}"
            with self.lock:
                self.subscribed[symbol] = topic
            return True, topic
        except Exception as exc:
            self.last_error = f"AddSubscription: {exc}"
            return False, self.last_error

    def snapshot(self, symbol: str) -> Dict[str, Any]:
        now = time.time()
        with self.lock:
            data = {p: list(self.buffers[symbol][p]) for p in PERIODS}
            ages = {
                p: (max(0.0, now - self.last_update[symbol].get(p, 0.0)) if self.last_update[symbol].get(p, 0.0) > 0 else None)
                for p in PERIODS
            }
            topic = self.subscribed.get(symbol)
            err = self.last_error
        caps = {
            p: {
                "available": bool(data[p]),
                "subscription_id": topic,
                "error": "" if data[p] else err,
                "source": "external_level2api" if data[p] else None,
            }
            for p in PERIODS
        }
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
            "provider": "level2api",
            "symbol": symbol,
            "runtime_available": self.runtime_available,
            "runtime_error": err,
            "summary": summary,
            "capabilities": caps,
            "counts": {p: len(data[p]) for p in PERIODS},
            "age_seconds": ages,
            "recent_transactions": data["l2transaction"][-120:],
            "recent_orders": data["l2order"][-120:],
            "quoteaux": data["l2quoteaux"][-1] if data["l2quoteaux"] else {},
            "orderqueue": data["l2orderqueue"][-1] if data["l2orderqueue"] else {},
        }


_HUB: Optional[_Hub] = None
_HUB_LOCK = threading.Lock()


def _hub() -> _Hub:
    global _HUB
    with _HUB_LOCK:
        if _HUB is None:
            _HUB = _Hub()
        return _HUB


class ExternalLevel2Manager:
    """QMTLevel2Manager-compatible view over the shared Level2API hub."""

    def __init__(self) -> None:
        self.symbol = ""
        self._hub = _hub()
        self.buffers = {p: deque() for p in PERIODS}  # compatibility for V5 initializer
        self.capabilities = {p: {"available": False, "subscription_id": None, "error": "", "source": None} for p in PERIODS}

    @property
    def available_runtime(self) -> bool:
        return self._hub.runtime_available

    def switch(self, symbol: str) -> Dict[str, Any]:
        self.symbol = str(symbol).upper().strip()
        ok, detail = self._hub.ensure_subscription(self.symbol)
        snap = self.snapshot()
        if not ok:
            snap["runtime_error"] = detail
        return snap

    def refresh(self, force: bool = False) -> Dict[str, int]:
        # Push transport: receiver threads keep buffers current.
        return {}

    def stop(self) -> None:
        # The process-wide stream must stay alive for the other seven managers.
        self.symbol = ""

    def status(self) -> Dict[str, Any]:
        snap = self.snapshot()
        return {
            "provider": "level2api",
            "symbol": self.symbol,
            "runtime_available": snap.get("runtime_available"),
            "runtime_error": snap.get("runtime_error"),
            "capabilities": snap.get("capabilities"),
            "counts": snap.get("counts"),
            "available_count": sum(1 for x in (snap.get("capabilities") or {}).values() if x.get("available")),
            "core_ready": bool(
                (snap.get("capabilities") or {}).get("l2transaction", {}).get("available") and
                (snap.get("capabilities") or {}).get("l2order", {}).get("available")
            ),
        }

    def snapshot(self) -> Dict[str, Any]:
        if not self.symbol:
            return {
                "provider": "level2api", "symbol": "", "runtime_available": self.available_runtime,
                "runtime_error": self._hub.last_error, "summary": analyze_level2(),
                "capabilities": self.capabilities, "counts": {p: 0 for p in PERIODS},
                "age_seconds": {p: None for p in PERIODS}, "recent_transactions": [],
                "recent_orders": [], "quoteaux": {}, "orderqueue": {},
            }
        return self._hub.snapshot(self.symbol)
