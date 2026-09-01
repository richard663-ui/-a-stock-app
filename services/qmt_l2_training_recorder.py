# -*- coding: utf-8 -*-
"""Persistent QMT Level-2 training recorder for 60-second research.

This service is research-only. It does not place orders and does not change the
mobile model. It records synchronized tick + Level-2 features every 5 seconds,
persists raw transaction/order events, and labels each sample with:
- point-in-time last-price return at 60s (legacy comparison)
- point-in-time mid-price returns at 5/15/30/60s
- smoothed future mid-price return using observations from +55s to +65s

The smoothed mid-price label is the primary trainable target because it reduces
bid/ask-bounce noise while keeping the prediction horizon centered on 60s.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xtquant import xtdata

from modules.direction_v18 import analyze_direction_v18
from modules.qmt_level2 import QMTLevel2Manager
from modules.qmt_live import normalize_tick

RECORDER_VERSION = "l2-training-recorder-v1-20260901"
DATA_ROOT = Path(os.environ.get("ASTOCK_RESEARCH_DATA_DIR", str(Path.home() / "AStockData"))).expanduser()
PERSIST_DIR = Path.home() / ".a_stock_qmt"
TRAINING_WATCHLIST = PERSIST_DIR / "training_watchlist.txt"
RESEARCH_WATCHLIST = PERSIST_DIR / "research_watchlist.txt"
STATUS_PATH = DATA_ROOT / "training" / "training_status.json"

POLL_SECONDS = 0.50
SAMPLE_SECONDS = 5
WATCHLIST_RELOAD_SECONDS = 10.0
L2_SNAPSHOT_SECONDS = 1.0
STATUS_SECONDS = 10.0
MAX_SYMBOLS = 8
PRICE_HISTORY_SECONDS = 180
LABEL_FINALIZE_AFTER = 66.0
PRIMARY_SYMBOL = "301236.SZ"


def _f(v: Any, d: Optional[float] = None) -> Optional[float]:
    try:
        x = float(v)
        return x if x == x else d
    except Exception:
        return d


def _i(v: Any, d: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return d


def _json(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)


def _normalize_symbol(value: str) -> str:
    text = str(value or "").strip().upper()
    if not text or text.startswith("#"):
        return ""
    if "." in text:
        code, market = text.split(".", 1)
        return f"{code}.{market}" if len(code) == 6 and code.isdigit() and market in {"SZ", "SH", "BJ"} else ""
    if len(text) != 6 or not text.isdigit():
        return ""
    if text.startswith(("6", "5", "9")):
        return text + ".SH"
    if text.startswith(("4", "8")):
        return text + ".BJ"
    return text + ".SZ"


def _read_symbols(path: Path) -> List[str]:
    if not path.exists():
        return []
    out: List[str] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = _normalize_symbol(line)
        if s and s not in out:
            out.append(s)
    return out


def _load_watchlist() -> List[str]:
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    if not TRAINING_WATCHLIST.exists():
        TRAINING_WATCHLIST.write_text(
            "# AStock Level-2 training watchlist\n"
            "# 301236.SZ is the priority research stock.\n"
            "301236.SZ\n",
            encoding="utf-8",
        )
    symbols = _read_symbols(TRAINING_WATCHLIST)
    for s in _read_symbols(RESEARCH_WATCHLIST):
        if s not in symbols:
            symbols.append(s)
    if PRIMARY_SYMBOL not in symbols:
        symbols.insert(0, PRIMARY_SYMBOL)
    return symbols[:MAX_SYMBOLS]


def _market_open(now: Optional[datetime] = None) -> bool:
    d = now or datetime.now()
    if d.weekday() >= 5:
        return False
    m = d.hour * 60 + d.minute
    return (570 <= m < 690) or (780 <= m < 900)


def _session(now: Optional[datetime] = None) -> str:
    d = now or datetime.now()
    m = d.hour * 60 + d.minute
    if 570 <= m < 690:
        return "AM"
    if 780 <= m < 900:
        return "PM"
    return "CLOSED"


def _arr(v: Any) -> List[float]:
    if not isinstance(v, (list, tuple)):
        return []
    out: List[float] = []
    for x in v:
        fx = _f(x)
        if fx is not None:
            out.append(float(fx))
    return out


def _book_values(row: Dict[str, Any]) -> Dict[str, float]:
    bp, ap = _arr(row.get("bidPrice")), _arr(row.get("askPrice"))
    bv, av = _arr(row.get("bidVol")), _arr(row.get("askVol"))
    bid1 = bp[0] if bp else float(_f(row.get("bidPrice1"), 0.0) or 0.0)
    ask1 = ap[0] if ap else float(_f(row.get("askPrice1"), 0.0) or 0.0)
    last = float(_f(row.get("lastPrice"), 0.0) or 0.0)
    mid = (bid1 + ask1) / 2.0 if bid1 > 0 and ask1 >= bid1 else last
    spread_pct = ((ask1 - bid1) / mid * 100.0) if mid > 0 and ask1 >= bid1 > 0 else 0.0
    b5, a5 = sum(bv[:5]), sum(av[:5])
    depth5_imb = (b5 - a5) / (b5 + a5) * 100.0 if b5 + a5 > 0 else 0.0
    micro = mid
    if bid1 > 0 and ask1 > 0 and bv and av and bv[0] + av[0] > 0:
        micro = (ask1 * bv[0] + bid1 * av[0]) / (bv[0] + av[0])
    micro_bias_pct = (micro / mid - 1.0) * 100.0 if mid > 0 else 0.0
    return {
        "last_price": last, "bid1": bid1, "ask1": ask1, "mid_price": mid,
        "spread_pct": spread_pct, "depth5_bid": b5, "depth5_ask": a5,
        "depth5_imbalance_pct": depth5_imb, "microprice": micro,
        "microprice_vs_mid_pct": micro_bias_pct,
    }


def _event_key(symbol: str, period: str, row: Dict[str, Any]) -> str:
    identity = None
    for k in ("tradeIndex", "entrustNo", "index", "time", "stime"):
        if row.get(k) not in (None, ""):
            identity = f"{k}:{row.get(k)}"
            break
    raw = f"{symbol}|{period}|{identity or _json(row)}"
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def _label_class(ret_pct: Optional[float], threshold_pct: float) -> Optional[int]:
    if ret_pct is None:
        return None
    if ret_pct > threshold_pct:
        return 1
    if ret_pct < -threshold_pct:
        return -1
    return 0


class DailyStore:
    def __init__(self) -> None:
        self.day = ""
        self.conn: Optional[sqlite3.Connection] = None

    def _path(self, day: str) -> Path:
        folder = DATA_ROOT / "training" / day
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "l2_training.sqlite3"

    def ensure(self) -> sqlite3.Connection:
        day = datetime.now().strftime("%Y-%m-%d")
        if self.conn is not None and day == self.day:
            return self.conn
        if self.conn is not None:
            try:
                self.conn.commit(); self.conn.close()
            except Exception:
                pass
        conn = sqlite3.connect(self._path(day), timeout=15.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS training_samples_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                sample_bucket INTEGER NOT NULL,
                generated_ts REAL NOT NULL,
                generated_at TEXT NOT NULL,
                session TEXT NOT NULL,
                last_price REAL NOT NULL,
                bid1 REAL,
                ask1 REAL,
                mid_price REAL NOT NULL,
                spread_pct REAL,
                label_threshold_pct REAL,
                true_l2 INTEGER NOT NULL DEFAULT 0,
                core_l2_ready INTEGER NOT NULL DEFAULT 0,
                l2_available_count INTEGER NOT NULL DEFAULT 0,
                baseline_direction TEXT,
                baseline_agreement INTEGER,
                baseline_high_confidence INTEGER NOT NULL DEFAULT 0,
                features_json TEXT NOT NULL,
                mid_5 REAL, mid_15 REAL, mid_30 REAL, mid_60 REAL,
                ret_mid_5_pct REAL, ret_mid_15_pct REAL, ret_mid_30_pct REAL, ret_mid_60_pct REAL,
                last_60 REAL, ret_last_60_pct REAL,
                smoothed_mid_60 REAL, ret_smoothed_mid_60_pct REAL,
                label_last_60 INTEGER, label_mid_60 INTEGER, label_smoothed_mid_60 INTEGER,
                labeled_at TEXT, valid INTEGER NOT NULL DEFAULT 0,
                invalid_reason TEXT,
                recorder_version TEXT NOT NULL,
                UNIQUE(symbol,sample_bucket,recorder_version)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_train_v1_symbol_time ON training_samples_v1(symbol,generated_ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_train_v1_label ON training_samples_v1(valid,label_smoothed_mid_60)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS l2_events_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                period TEXT NOT NULL,
                event_key TEXT NOT NULL UNIQUE,
                captured_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                recorder_version TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_l2event_v1_symbol_period ON l2_events_v1(symbol,period,id)")
        conn.commit()
        self.conn, self.day = conn, day
        return conn

    def insert_sample(self, sample: Dict[str, Any]) -> bool:
        c = self.ensure()
        cur = c.execute("""
            INSERT OR IGNORE INTO training_samples_v1 (
                symbol,sample_bucket,generated_ts,generated_at,session,last_price,bid1,ask1,mid_price,
                spread_pct,label_threshold_pct,true_l2,core_l2_ready,l2_available_count,
                baseline_direction,baseline_agreement,baseline_high_confidence,features_json,recorder_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            sample["symbol"], sample["sample_bucket"], sample["generated_ts"], sample["generated_at"],
            sample["session"], sample["last_price"], sample["bid1"], sample["ask1"], sample["mid_price"],
            sample["spread_pct"], sample["label_threshold_pct"], 1 if sample["true_l2"] else 0,
            1 if sample["core_l2_ready"] else 0, sample["l2_available_count"], sample["baseline_direction"],
            sample["baseline_agreement"], 1 if sample["baseline_high_confidence"] else 0,
            _json(sample["features"]), RECORDER_VERSION,
        ))
        c.commit()
        return cur.rowcount > 0

    def insert_events(self, symbol: str, period: str, rows: Iterable[Dict[str, Any]]) -> int:
        c = self.ensure()
        n = 0
        now_text = datetime.now().astimezone().isoformat(timespec="milliseconds")
        for row in rows:
            if not isinstance(row, dict) or not row:
                continue
            cur = c.execute(
                "INSERT OR IGNORE INTO l2_events_v1(symbol,period,event_key,captured_at,payload_json,recorder_version) VALUES(?,?,?,?,?,?)",
                (symbol, period, _event_key(symbol, period, row), now_text, _json(row), RECORDER_VERSION),
            )
            n += max(0, cur.rowcount)
        if n:
            c.commit()
        return n

    def finalize(self, sample: Dict[str, Any], labels: Dict[str, Any]) -> None:
        c = self.ensure()
        c.execute("""
            UPDATE training_samples_v1 SET
                mid_5=?,mid_15=?,mid_30=?,mid_60=?,
                ret_mid_5_pct=?,ret_mid_15_pct=?,ret_mid_30_pct=?,ret_mid_60_pct=?,
                last_60=?,ret_last_60_pct=?,smoothed_mid_60=?,ret_smoothed_mid_60_pct=?,
                label_last_60=?,label_mid_60=?,label_smoothed_mid_60=?,
                labeled_at=?,valid=?,invalid_reason=?
            WHERE symbol=? AND sample_bucket=? AND recorder_version=?
        """, (
            labels.get("mid_5"), labels.get("mid_15"), labels.get("mid_30"), labels.get("mid_60"),
            labels.get("ret_mid_5_pct"), labels.get("ret_mid_15_pct"), labels.get("ret_mid_30_pct"), labels.get("ret_mid_60_pct"),
            labels.get("last_60"), labels.get("ret_last_60_pct"), labels.get("smoothed_mid_60"), labels.get("ret_smoothed_mid_60_pct"),
            labels.get("label_last_60"), labels.get("label_mid_60"), labels.get("label_smoothed_mid_60"),
            datetime.now().astimezone().isoformat(timespec="seconds"), 1 if labels.get("valid") else 0,
            labels.get("invalid_reason"), sample["symbol"], sample["sample_bucket"], RECORDER_VERSION,
        ))
        c.commit()


def _nearest(history: Deque[Tuple[float, float, float]], target: float, tolerance: float = 3.0) -> Optional[Tuple[float, float]]:
    best = None
    best_gap = 1e9
    for ts, mid, last in history:
        gap = abs(ts - target)
        if gap <= tolerance and gap < best_gap:
            best, best_gap = (mid, last), gap
    return best


def _make_labels(sample: Dict[str, Any], history: Deque[Tuple[float, float, float]]) -> Dict[str, Any]:
    entry_mid = float(sample["mid_price"])
    entry_last = float(sample["last_price"])
    t0 = float(sample["generated_ts"])
    out: Dict[str, Any] = {"valid": False, "invalid_reason": "missing_horizon_prices"}
    for h in (5, 15, 30, 60):
        point = _nearest(history, t0 + h, tolerance=3.0)
        mid = point[0] if point else None
        out[f"mid_{h}"] = mid
        out[f"ret_mid_{h}_pct"] = ((mid / entry_mid - 1.0) * 100.0) if mid and entry_mid > 0 else None
        if h == 60:
            last = point[1] if point else None
            out["last_60"] = last
            out["ret_last_60_pct"] = ((last / entry_last - 1.0) * 100.0) if last and entry_last > 0 else None
    smooth = [mid for ts, mid, _ in history if t0 + 55.0 <= ts <= t0 + 65.0 and mid > 0]
    out["smoothed_mid_60"] = sum(smooth) / len(smooth) if len(smooth) >= 3 else None
    out["ret_smoothed_mid_60_pct"] = (
        (out["smoothed_mid_60"] / entry_mid - 1.0) * 100.0
        if out["smoothed_mid_60"] and entry_mid > 0 else None
    )
    threshold = float(sample["label_threshold_pct"])
    out["label_last_60"] = _label_class(out.get("ret_last_60_pct"), threshold)
    out["label_mid_60"] = _label_class(out.get("ret_mid_60_pct"), threshold)
    out["label_smoothed_mid_60"] = _label_class(out.get("ret_smoothed_mid_60_pct"), threshold)
    required = [out.get("mid_60"), out.get("smoothed_mid_60"), out.get("ret_smoothed_mid_60_pct")]
    if all(v is not None for v in required):
        out["valid"] = True
        out["invalid_reason"] = None
    return out


def _extract_features(symbol: str, row: Dict[str, Any], snapshot: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    book = _book_values(row)
    summary = dict(snapshot.get("summary") or {})
    caps = dict(snapshot.get("capabilities") or {})
    direction = analyze_direction_v18(__import__("pandas").DataFrame([row]), summary)
    dm = dict(direction.get("metrics") or {})
    lm = dict(summary.get("metrics") or {})
    now = datetime.now()
    last_close = float(_f(row.get("lastClose"), 0.0) or 0.0)
    last = book["last_price"]
    high = float(_f(row.get("high"), 0.0) or 0.0)
    low = float(_f(row.get("low"), 0.0) or 0.0)
    features: Dict[str, Any] = {
        "minute_of_day": now.hour * 60 + now.minute + now.second / 60.0,
        "session_am": 1 if _session(now) == "AM" else 0,
        "last_price": last,
        "spread_pct": book["spread_pct"],
        "depth5_imbalance_pct": book["depth5_imbalance_pct"],
        "microprice_vs_mid_pct": book["microprice_vs_mid_pct"],
        "day_return_pct": ((last / last_close - 1.0) * 100.0) if last_close > 0 else 0.0,
        "distance_high_pct": ((last / high - 1.0) * 100.0) if high > 0 else 0.0,
        "distance_low_pct": ((last / low - 1.0) * 100.0) if low > 0 else 0.0,
        "volume": float(_f(row.get("volume"), 0.0) or 0.0),
        "amount": float(_f(row.get("amount"), 0.0) or 0.0),
        "change_10s_pct": float(_f(dm.get("change_10s_pct"), 0.0) or 0.0),
        "change_30s_pct": float(_f(dm.get("change_30s_pct"), 0.0) or 0.0),
        "change_60s_pct": float(_f(dm.get("change_60s_pct"), 0.0) or 0.0),
        "change_120s_pct": float(_f(dm.get("change_120s_pct"), 0.0) or 0.0),
        "above_vwap_pct": float(_f(dm.get("above_vwap_pct"), 0.0) or 0.0),
        "tick_buy_pct": float(_f(dm.get("buy_pct"), 50.0) or 50.0),
        "book_buy_pressure_pct": float(_f(dm.get("buy_pressure_pct"), 50.0) or 50.0),
        "pressure_change_pct": float(_f(dm.get("pressure_change_pct"), 0.0) or 0.0),
        "l2_active_buy_pct": float(_f(lm.get("active_buy_pct"), 50.0) or 50.0),
        "l2_big_buy_pct": float(_f(lm.get("big_buy_pct"), 50.0) or 50.0),
        "l2_order_buy_pct": float(_f(lm.get("order_buy_pct"), 50.0) or 50.0),
        "l2_cancel_sell_support_pct": float(_f(lm.get("cancel_sell_support_pct"), 50.0) or 50.0),
        "l2_total_book_buy_pct": float(_f(lm.get("total_book_buy_pct"), 50.0) or 50.0),
        "l2_depth10_buy_pct": float(_f(lm.get("depth10_buy_pct"), 50.0) or 50.0),
        "l2_queue_buy_pct": float(_f(lm.get("queue_buy_pct"), 50.0) or 50.0),
        "l2_ddx": float(_f(lm.get("ddx"), 0.0) or 0.0),
        "l2_ddy": float(_f(lm.get("ddy"), 0.0) or 0.0),
        "l2_ddz": float(_f(lm.get("ddz"), 0.0) or 0.0),
        "l2_net_order": float(_f(lm.get("net_order"), 0.0) or 0.0),
        "l2_agreement": int(_i(summary.get("agreement"), 0)),
        "l2_up_votes": int(_i(summary.get("up_votes"), 0)),
        "l2_down_votes": int(_i(summary.get("down_votes"), 0)),
        "available_l2quote": int(bool((summary.get("available") or {}).get("l2quote"))),
        "available_l2transaction": int(bool((summary.get("available") or {}).get("l2transaction"))),
        "available_l2order": int(bool((summary.get("available") or {}).get("l2order"))),
        "available_l2quoteaux": int(bool((summary.get("available") or {}).get("l2quoteaux"))),
        "available_l2transactioncount": int(bool((summary.get("available") or {}).get("l2transactioncount"))),
        "available_l2orderqueue": int(bool((summary.get("available") or {}).get("l2orderqueue"))),
        "baseline_agreement": int(_i(direction.get("condition_agreement"), 0)),
        "baseline_high_confidence": int(bool(direction.get("high_confidence"))),
        "baseline_selective_gate": int(bool(dm.get("selective_gate_60"))),
    }
    available_count = sum(1 for x in caps.values() if isinstance(x, dict) and x.get("available"))
    meta = {
        "book": book,
        "direction": direction,
        "summary": summary,
        "l2_available_count": available_count,
        "true_l2": bool((summary.get("available") or {}).get("l2transaction")),
        "core_l2_ready": bool(dm.get("core_l2_ready")),
    }
    return features, meta


def _persist_snapshot_events(store: DailyStore, symbol: str, snap: Dict[str, Any]) -> int:
    n = 0
    n += store.insert_events(symbol, "l2transaction", snap.get("recent_transactions") or [])
    n += store.insert_events(symbol, "l2order", snap.get("recent_orders") or [])
    aux = snap.get("quoteaux") or {}
    oq = snap.get("orderqueue") or {}
    if aux:
        n += store.insert_events(symbol, "l2quoteaux", [aux])
    if oq:
        n += store.insert_events(symbol, "l2orderqueue", [oq])
    return n


def _write_status(payload: Dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATUS_PATH)


def main() -> None:
    print("AStock Level-2 training recorder started")
    print(f"Recorder: {RECORDER_VERSION}")
    print("Primary target: 60s smoothed mid-price; legacy lastPrice label kept for comparison")
    print(f"Training watchlist: {TRAINING_WATCHLIST}")
    store = DailyStore()
    managers: Dict[str, QMTLevel2Manager] = {}
    price_history: Dict[str, Deque[Tuple[float, float, float]]] = {}
    pending: Dict[Tuple[str, int], Dict[str, Any]] = {}
    last_sample_bucket: Dict[str, int] = {}
    latest_rows: Dict[str, Dict[str, Any]] = {}
    sample_counts: Dict[str, int] = {}
    label_counts: Dict[str, int] = {}
    event_counts: Dict[str, int] = {}
    last_symbols_reload = 0.0
    last_l2_snapshot = 0.0
    last_status = 0.0
    symbols: List[str] = []

    try:
        while True:
            loop_ts = time.time()
            now_dt = datetime.now()
            if loop_ts - last_symbols_reload >= WATCHLIST_RELOAD_SECONDS:
                symbols = _load_watchlist()
                last_symbols_reload = loop_ts
                for s in symbols:
                    if s not in managers:
                        mgr = QMTLevel2Manager()
                        mgr.switch(s)
                        managers[s] = mgr
                        price_history[s] = deque()
                        sample_counts.setdefault(s, 0); label_counts.setdefault(s, 0); event_counts.setdefault(s, 0)
                        print(f"[L2 ADD] {s}")
                for s in list(managers):
                    if s not in symbols:
                        managers.pop(s).stop()
                        price_history.pop(s, None)
                        print(f"[L2 DROP] {s}")

            if symbols:
                try:
                    ticks = xtdata.get_full_tick(symbols) or {}
                except Exception as exc:
                    print(f"[WARN] get_full_tick: {exc}")
                    ticks = {}
                for s in symbols:
                    raw = ticks.get(s) or {}
                    if not raw:
                        continue
                    row = normalize_tick(s, raw)
                    latest_rows[s] = row
                    b = _book_values(row)
                    if b["mid_price"] > 0:
                        hist = price_history.setdefault(s, deque())
                        hist.append((loop_ts, b["mid_price"], b["last_price"]))
                        cutoff = loop_ts - PRICE_HISTORY_SECONDS
                        while hist and hist[0][0] < cutoff:
                            hist.popleft()

            snapshots: Dict[str, Dict[str, Any]] = {}
            if loop_ts - last_l2_snapshot >= L2_SNAPSHOT_SECONDS:
                for s in symbols:
                    try:
                        snap = managers[s].snapshot()
                        snapshots[s] = snap
                        event_counts[s] = event_counts.get(s, 0) + _persist_snapshot_events(store, s, snap)
                    except Exception as exc:
                        print(f"[WARN] L2 snapshot {s}: {exc}")
                last_l2_snapshot = loop_ts

            if _market_open(now_dt):
                sample_bucket = int(loop_ts // SAMPLE_SECONDS) * SAMPLE_SECONDS
                for s in symbols:
                    row = latest_rows.get(s)
                    if not row or sample_bucket == last_sample_bucket.get(s):
                        continue
                    snap = snapshots.get(s)
                    if snap is None:
                        try:
                            snap = managers[s].snapshot()
                        except Exception:
                            continue
                    try:
                        features, meta = _extract_features(s, row, snap)
                    except Exception as exc:
                        print(f"[WARN] feature build {s}: {exc}")
                        continue
                    book = meta["book"]
                    if book["mid_price"] <= 0 or book["last_price"] <= 0:
                        continue
                    threshold = max(0.01, book["spread_pct"] * 0.75)
                    direction = meta["direction"]
                    sample = {
                        "symbol": s, "sample_bucket": sample_bucket, "generated_ts": loop_ts,
                        "generated_at": datetime.fromtimestamp(loop_ts).astimezone().isoformat(timespec="milliseconds"),
                        "session": _session(now_dt), "last_price": book["last_price"], "bid1": book["bid1"],
                        "ask1": book["ask1"], "mid_price": book["mid_price"], "spread_pct": book["spread_pct"],
                        "label_threshold_pct": threshold, "true_l2": meta["true_l2"],
                        "core_l2_ready": meta["core_l2_ready"], "l2_available_count": meta["l2_available_count"],
                        "baseline_direction": direction.get("direction_60"),
                        "baseline_agreement": int(_i(direction.get("condition_agreement"), 0)),
                        "baseline_high_confidence": bool(direction.get("high_confidence")),
                        "features": features,
                    }
                    if store.insert_sample(sample):
                        pending[(s, sample_bucket)] = sample
                        sample_counts[s] = sample_counts.get(s, 0) + 1
                    last_sample_bucket[s] = sample_bucket

            for key, sample in list(pending.items()):
                if loop_ts < float(sample["generated_ts"]) + LABEL_FINALIZE_AFTER:
                    continue
                labels = _make_labels(sample, price_history.get(sample["symbol"], deque()))
                store.finalize(sample, labels)
                if labels.get("valid"):
                    label_counts[sample["symbol"]] = label_counts.get(sample["symbol"], 0) + 1
                pending.pop(key, None)

            if loop_ts - last_status >= STATUS_SECONDS:
                payload = {
                    "ok": True,
                    "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "recorder_version": RECORDER_VERSION,
                    "symbols": symbols,
                    "primary_symbol": PRIMARY_SYMBOL,
                    "market_open": _market_open(),
                    "sample_counts_today": sample_counts,
                    "labeled_counts_today": label_counts,
                    "raw_l2_events_today": event_counts,
                    "pending_labels": len(pending),
                    "data_root": str(DATA_ROOT / "training"),
                    "training_watchlist": str(TRAINING_WATCHLIST),
                }
                _write_status(payload)
                print(
                    f"{datetime.now().strftime('%H:%M:%S')} symbols={len(symbols)} "
                    f"samples={sum(sample_counts.values())} labeled={sum(label_counts.values())} "
                    f"l2events={sum(event_counts.values())} pending={len(pending)}"
                )
                last_status = loop_ts

            time.sleep(max(0.05, POLL_SECONDS - (time.time() - loop_ts)))
    except KeyboardInterrupt:
        print("Level-2 training recorder stopped")
    finally:
        for mgr in managers.values():
            try:
                mgr.stop()
            except Exception:
                pass
        if store.conn is not None:
            try:
                store.conn.commit(); store.conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
