# -*- coding: utf-8 -*-
"""Persistent QMT research recorder for future 60s/120s quant research.

Records three layers independently:
1) raw QMT five-level snapshots (local only),
2) a 5-second research score trace (local only),
3) non-overlapping 60s/120s forward-evaluation samples (local + small cloud rows).

The recorder is independent from the mobile UI. It follows a fixed watchlist and
also the stock currently selected on the phone. Research data are never put in
GitHub. Only scored evaluation rows are uploaded so results can be reviewed
remotely without uploading the raw market database.
"""
from __future__ import annotations

import json
import os
import queue
import sqlite3
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xtquant import xtdata

from modules.cloud_bridge import CloudBridge, load_bridge_config
from modules.qmt_live import normalize_tick
from modules.research_forward_model import MODEL_VERSION, high_confidence, score_label, score_rows

PERSIST_DIR = Path.home() / ".a_stock_qmt"
WATCHLIST_PATH = PERSIST_DIR / "research_watchlist.txt"
DATA_ROOT = Path(os.environ.get("ASTOCK_RESEARCH_DATA_DIR", str(Path.home() / "AStockData"))).expanduser()
STATUS_PATH = DATA_ROOT / "recorder_status.json"
POLL_SECONDS = 0.25
WATCHLIST_RELOAD_SECONDS = 5.0
MOBILE_SYMBOL_POLL_SECONDS = 6.0
MACD_POLL_SECONDS = 25.0
DB_COMMIT_SECONDS = 1.0
TRACE_SECONDS = 5
MAX_SYMBOLS = 60
BUFFER_ROWS = 2400


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


def _load_watchlist() -> Set[str]:
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    if not WATCHLIST_PATH.exists():
        WATCHLIST_PATH.write_text(
            "# AStock research watchlist\n"
            "# One symbol per line, e.g. 000400.SZ or 600522.SH\n"
            "# The current phone-selected stock is recorded automatically too.\n",
            encoding="utf-8",
        )
        return set()
    out: Set[str] = set()
    for line in WATCHLIST_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = _normalize_symbol(line)
        if s:
            out.add(s)
    return out


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _f(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        x = float(v)
        return x if x == x else default
    except Exception:
        return default


def _i(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(v)
    except Exception:
        return default


def _row_ts(row: Dict[str, Any]) -> float:
    try:
        return datetime.fromisoformat(str(row.get("captured_at") or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return time.time()


def _session_key(now: Optional[datetime] = None) -> str:
    d = now or datetime.now()
    if d.weekday() >= 5:
        return "CLOSED"
    m = d.hour * 60 + d.minute
    if 570 <= m < 690:
        return d.strftime("%Y-%m-%d") + "-AM"
    if 780 <= m < 900:
        return d.strftime("%Y-%m-%d") + "-PM"
    return "CLOSED"


def _seconds_to_close(now: Optional[datetime] = None) -> int:
    d = now or datetime.now()
    m = d.hour * 60 + d.minute
    sec = d.second
    if 570 <= m < 690:
        return (690 - m) * 60 - sec
    if 780 <= m < 900:
        return (900 - m) * 60 - sec
    return 0


def _strength(score: int) -> str:
    a = abs(int(score))
    if a >= 70:
        return "strong"
    if a >= 40:
        return "medium"
    if a >= 15:
        return "light"
    return "neutral"


@dataclass
class DailyStore:
    day: str = ""
    conn: Optional[sqlite3.Connection] = None
    pending_writes: int = 0
    last_commit: float = 0.0

    def _path(self, day: str) -> Path:
        folder = DATA_ROOT / "raw" / day
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "ticks.sqlite3"

    def _open(self, day: str) -> sqlite3.Connection:
        if self.conn is not None:
            try:
                self.conn.commit(); self.conn.close()
            except Exception:
                pass
        conn = sqlite3.connect(self._path(day), timeout=15.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL, captured_at TEXT NOT NULL, qmt_time INTEGER, timetag TEXT,
                last_price REAL, open REAL, high REAL, low REAL, last_close REAL, avg_price REAL,
                amount REAL, volume INTEGER, bid_price_json TEXT NOT NULL, ask_price_json TEXT NOT NULL,
                bid_vol_json TEXT NOT NULL, ask_vol_json TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'QMT_L1_FIVE_LEVEL', recorded_at TEXT NOT NULL,
                UNIQUE(symbol,captured_at,last_price,volume,amount)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_ticks_symbol_time ON raw_ticks(symbol,captured_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS score_trace (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL, captured_at TEXT NOT NULL, price REAL NOT NULL,
                score60 INTEGER NOT NULL, score120 INTEGER NOT NULL,
                direction60 TEXT NOT NULL, direction120 TEXT NOT NULL,
                strength60 TEXT NOT NULL, strength120 TEXT NOT NULL,
                macd_bias REAL, features_json TEXT NOT NULL, model_version TEXT NOT NULL,
                UNIQUE(symbol,captured_at,model_version)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_score_trace_symbol_time ON score_trace(symbol,captured_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS forward_eval (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL, horizon_seconds INTEGER NOT NULL, bucket_start INTEGER NOT NULL,
                generated_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                entry_price REAL NOT NULL, exit_price REAL, return_pct REAL,
                direction TEXT NOT NULL, score INTEGER NOT NULL, score_abs INTEGER NOT NULL,
                tier TEXT NOT NULL, high_confidence INTEGER NOT NULL DEFAULT 0,
                flat INTEGER, correct INTEGER, macd_bias REAL,
                features_json TEXT NOT NULL, model_version TEXT NOT NULL, scored_at TEXT,
                UNIQUE(symbol,horizon_seconds,bucket_start,model_version)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_forward_eval_symbol_time ON forward_eval(symbol,horizon_seconds,bucket_start)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recorder_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        self.day, self.conn, self.pending_writes, self.last_commit = day, conn, 0, time.time()
        return conn

    def ensure(self) -> sqlite3.Connection:
        day = datetime.now().strftime("%Y-%m-%d")
        return self._open(day) if self.conn is None or self.day != day else self.conn

    def _maybe_commit(self) -> None:
        if self.conn is None:
            return
        now = time.time()
        if self.pending_writes >= 100 or now - self.last_commit >= DB_COMMIT_SECONDS:
            self.conn.commit(); self.pending_writes = 0; self.last_commit = now

    def write_tick(self, row: Dict[str, Any]) -> bool:
        conn = self.ensure()
        cur = conn.execute("""
            INSERT OR IGNORE INTO raw_ticks (
                symbol,captured_at,qmt_time,timetag,last_price,open,high,low,last_close,avg_price,
                amount,volume,bid_price_json,ask_price_json,bid_vol_json,ask_vol_json,recorded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            str(row.get("symbol") or ""), str(row.get("captured_at") or ""), _i(row.get("time")),
            str(row.get("timetag") or ""), _f(row.get("lastPrice")), _f(row.get("open")),
            _f(row.get("high")), _f(row.get("low")), _f(row.get("lastClose")), _f(row.get("avgPrice")),
            _f(row.get("amount")), _i(row.get("volume")), _json(list(row.get("bidPrice") or [])),
            _json(list(row.get("askPrice") or [])), _json(list(row.get("bidVol") or [])),
            _json(list(row.get("askVol") or [])), datetime.now().isoformat(timespec="milliseconds"),
        ))
        if cur.rowcount > 0:
            self.pending_writes += 1
        self._maybe_commit()
        return cur.rowcount > 0

    def write_trace(self, symbol: str, captured_at: str, metrics: Dict[str, Any]) -> None:
        conn = self.ensure()
        d60, d120 = score_label(metrics["score60"]), score_label(metrics["score120"])
        conn.execute("""
            INSERT OR IGNORE INTO score_trace (
                symbol,captured_at,price,score60,score120,direction60,direction120,strength60,strength120,
                macd_bias,features_json,model_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            symbol, captured_at, metrics["price"], metrics["score60"], metrics["score120"],
            d60["direction"], d120["direction"], _strength(metrics["score60"]), _strength(metrics["score120"]),
            metrics.get("macd_bias"), _json(metrics), MODEL_VERSION,
        ))
        self.pending_writes += 1; self._maybe_commit()

    def create_eval(self, sample: Dict[str, Any]) -> bool:
        conn = self.ensure()
        cur = conn.execute("""
            INSERT OR IGNORE INTO forward_eval (
                symbol,horizon_seconds,bucket_start,generated_at,expires_at,entry_price,direction,score,score_abs,
                tier,high_confidence,macd_bias,features_json,model_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            sample["symbol"], sample["horizon_seconds"], sample["bucket_start"], sample["generated_at"],
            sample["expires_at"], sample["entry_price"], sample["direction"], sample["score"], abs(sample["score"]),
            sample["tier"], 1 if sample["high_confidence"] else 0, sample.get("macd_bias"),
            _json(sample["features"]), MODEL_VERSION,
        ))
        if cur.rowcount > 0:
            self.pending_writes += 1
        self._maybe_commit()
        return cur.rowcount > 0

    def score_eval(self, sample: Dict[str, Any], exit_price: float, scored_at: str) -> Dict[str, Any]:
        entry = float(sample["entry_price"])
        ret = (exit_price / entry - 1.0) * 100.0
        flat_band = max(0.01, float(sample["features"].get("noise_pct") or 0.02))
        flat = abs(ret) < flat_band
        if flat or sample["direction"] == "WATCH":
            correct = None
        else:
            correct = ret > 0 if sample["direction"] == "UP" else ret < 0
        conn = self.ensure()
        conn.execute("""
            UPDATE forward_eval SET exit_price=?,return_pct=?,flat=?,correct=?,scored_at=?
            WHERE symbol=? AND horizon_seconds=? AND bucket_start=? AND model_version=?
        """, (
            exit_price, ret, 1 if flat else 0, None if correct is None else (1 if correct else 0), scored_at,
            sample["symbol"], sample["horizon_seconds"], sample["bucket_start"], MODEL_VERSION,
        ))
        self.pending_writes += 1; self._maybe_commit()
        return {**sample, "exit_price": exit_price, "return_pct": ret, "flat": flat, "correct": correct, "scored_at": scored_at}

    def heartbeat(self, payload: Dict[str, Any]) -> None:
        conn = self.ensure(); now_text = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO recorder_meta(key,value,updated_at) VALUES('heartbeat',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (_json(payload), now_text),
        )
        conn.commit(); self.pending_writes = 0; self.last_commit = time.time()


class CloudState:
    def __init__(self):
        self._lock = threading.Lock(); self._symbol = ""; self._macd: Dict[str, float] = {}
        self._stop = threading.Event(); self.bridge: Optional[CloudBridge] = None
        try:
            cfg = load_bridge_config()
            if cfg.ok:
                self.bridge = CloudBridge(cfg, timeout=3.0)
        except Exception:
            self.bridge = None
        threading.Thread(target=self._run, name="research-cloud-state", daemon=True).start()

    def mobile_symbol(self) -> str:
        with self._lock: return self._symbol

    def macd_bias(self, symbol: str) -> float:
        with self._lock: return float(self._macd.get(symbol, 0.0))

    def stop(self) -> None: self._stop.set()

    def _run(self) -> None:
        last_mobile = last_macd = 0.0
        while not self._stop.is_set():
            now = time.time()
            if self.bridge is not None and now - last_mobile >= MOBILE_SYMBOL_POLL_SECONDS:
                try:
                    s = _normalize_symbol(self.bridge.get_requested_symbol())
                    if s:
                        with self._lock: self._symbol = s
                except Exception: pass
                last_mobile = now
            if self.bridge is not None and now - last_macd >= MACD_POLL_SECONDS:
                try:
                    rows = self.bridge._request("GET", "mobile_macd_cache", params={"bridge_id": f"eq.{self.bridge.config.bridge_id}", "select": "symbol,payload"}) or []
                    biases = {str(r.get("symbol") or "").upper(): float((r.get("payload") or {}).get("bias_score") or 0.0) for r in rows}
                    with self._lock: self._macd = biases
                except Exception: pass
                last_macd = now
            self._stop.wait(1.0)


class EvaluationUploader:
    def __init__(self, bridge: Optional[CloudBridge]):
        self.bridge = bridge; self.q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=2000); self.stop_event = threading.Event()
        threading.Thread(target=self._run, name="research-eval-uploader", daemon=True).start()

    def submit(self, row: Dict[str, Any]) -> None:
        if self.bridge is None: return
        try: self.q.put_nowait(row)
        except queue.Full: pass

    def stop(self) -> None: self.stop_event.set()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try: row = self.q.get(timeout=1.0)
            except queue.Empty: continue
            if self.bridge is None: continue
            try:
                payload = {
                    "bridge_id": self.bridge.config.bridge_id,
                    "symbol": row["symbol"], "horizon_seconds": row["horizon_seconds"],
                    "bucket_start": datetime.fromtimestamp(row["bucket_start"]).astimezone().isoformat(),
                    "generated_at": row["generated_at"], "expires_at": row["expires_at"],
                    "entry_price": row["entry_price"], "exit_price": row.get("exit_price"),
                    "return_pct": row.get("return_pct"), "direction": row["direction"],
                    "score": row["score"], "score_abs": abs(row["score"]), "tier": row["tier"],
                    "high_confidence": bool(row["high_confidence"]), "flat": row.get("flat"),
                    "correct": row.get("correct"), "model_version": MODEL_VERSION,
                    "macd_bias": row.get("macd_bias"), "features": row["features"], "scored_at": row.get("scored_at"),
                }
                self.bridge._request(
                    "POST", "forward_eval_samples_v2?on_conflict=bridge_id,symbol,horizon_seconds,bucket_start",
                    json=payload, headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                )
            except Exception as exc:
                print(f"[WARN] evaluation upload failed: {exc}")


def _subscribe(symbol: str) -> Optional[int]:
    try: return xtdata.subscribe_quote(symbol, period="tick", count=-1)
    except Exception as exc:
        print(f"[WARN] subscribe {symbol}: {exc}"); return None


def _write_status(symbols: Iterable[str], counts: Dict[str, int], eval_counts: Dict[str, int], last_error: str = "") -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": not bool(last_error), "updated_at": datetime.now().isoformat(timespec="seconds"),
        "data_root": str(DATA_ROOT), "watchlist_file": str(WATCHLIST_PATH), "symbols": sorted(symbols),
        "rows_written_today": counts, "forward_eval_scored_today": eval_counts,
        "research_model": MODEL_VERSION, "last_error": last_error,
    }
    tmp = STATUS_PATH.with_suffix(".tmp"); tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(STATUS_PATH)


def main() -> None:
    print("AStock Research Recorder started")
    print("Raw source: QMT five-level snapshot ticks")
    print(f"Research model: {MODEL_VERSION}")
    print("Trace cadence: 5 seconds; evaluation: non-overlapping 60s/120s buckets")
    print(f"Data root: {DATA_ROOT}")

    store = DailyStore(); cloud = CloudState(); uploader = EvaluationUploader(cloud.bridge)
    subscriptions: Dict[str, Optional[int]] = {}; last_keys: Dict[str, Tuple] = {}; counts: Dict[str, int] = {}
    buffers: Dict[str, Deque[Dict[str, Any]]] = {}; last_trace_bucket: Dict[str, int] = {}
    pending: Dict[Tuple[str, int, int], Dict[str, Any]] = {}; eval_counts = {"60": 0, "120": 0}
    fixed: Set[str] = set(); last_watchlist_load = last_status = 0.0; last_error = ""; active_session = ""

    try:
        while True:
            now = time.time(); now_dt = datetime.now(); session = _session_key(now_dt)
            if session != active_session:
                if active_session and session != active_session:
                    buffers.clear(); last_trace_bucket.clear(); pending.clear()
                active_session = session

            if now - last_watchlist_load >= WATCHLIST_RELOAD_SECONDS:
                fixed = _load_watchlist(); last_watchlist_load = now
            symbols = set(fixed); mobile_symbol = cloud.mobile_symbol()
            if mobile_symbol: symbols.add(mobile_symbol)
            symbols = set(sorted(s for s in symbols if s)[:MAX_SYMBOLS])

            for symbol in sorted(symbols):
                if symbol not in subscriptions:
                    subscriptions[symbol] = _subscribe(symbol); counts.setdefault(symbol, 0); buffers[symbol] = deque(maxlen=BUFFER_ROWS); print(f"[ADD] {symbol}")
            for symbol in list(subscriptions):
                if symbol not in symbols:
                    sid = subscriptions.pop(symbol); last_keys.pop(symbol, None); buffers.pop(symbol, None)
                    if sid is not None:
                        try: xtdata.unsubscribe_quote(sid)
                        except Exception: pass
                    print(f"[DROP] {symbol}")

            if symbols:
                try:
                    data = xtdata.get_full_tick(sorted(symbols)) or {}
                    for symbol in symbols:
                        tick = data.get(symbol) or {}
                        if not tick: continue
                        row = normalize_tick(symbol, tick)
                        key = (row.get("captured_at"), row.get("lastPrice"), row.get("volume"), row.get("amount"), tuple(row.get("bidPrice") or []), tuple(row.get("askPrice") or []), tuple(row.get("bidVol") or []), tuple(row.get("askVol") or []))
                        if key == last_keys.get(symbol): continue
                        last_keys[symbol] = key
                        if store.write_tick(row): counts[symbol] = counts.get(symbol, 0) + 1
                        if session == "CLOSED": continue
                        buf = buffers.setdefault(symbol, deque(maxlen=BUFFER_ROWS)); buf.append(row)
                        row_time = _row_ts(row)

                        # Score matured independent evaluation samples with the first fresh tick after expiry.
                        for pkey, sample in list(pending.items()):
                            if sample["symbol"] != symbol or row_time < sample["expires_ts"]: continue
                            exit_price = _f(row.get("lastPrice"))
                            if not exit_price or exit_price <= 0: continue
                            scored = store.score_eval(sample, float(exit_price), datetime.now().astimezone().isoformat())
                            uploader.submit(scored); eval_counts[str(sample["horizon_seconds"])] += 1; pending.pop(pkey, None)

                        trace_bucket = int(row_time // TRACE_SECONDS)
                        if len(buf) >= 20 and trace_bucket != last_trace_bucket.get(symbol):
                            metrics = score_rows(list(buf), cloud.macd_bias(symbol)); last_trace_bucket[symbol] = trace_bucket
                            store.write_trace(symbol, str(row.get("captured_at") or ""), metrics)

                            # Each horizon gets its own non-overlapping fixed bucket. Do not create a sample
                            # that would mature during lunch or after the close.
                            for horizon in (60, 120):
                                if _seconds_to_close(now_dt) <= horizon + 3: continue
                                bucket = int(row_time // horizon) * horizon
                                pkey = (symbol, horizon, bucket)
                                if pkey in pending: continue
                                score = int(metrics["score60"] if horizon == 60 else metrics["score120"])
                                label = score_label(score); generated = datetime.fromtimestamp(row_time).astimezone().isoformat(); expires_ts = row_time + horizon
                                sample = {
                                    "symbol": symbol, "horizon_seconds": horizon, "bucket_start": bucket,
                                    "generated_at": generated, "expires_at": datetime.fromtimestamp(expires_ts).astimezone().isoformat(),
                                    "expires_ts": expires_ts, "entry_price": float(metrics["price"]), "direction": label["direction"],
                                    "score": score, "tier": label["tier"], "high_confidence": high_confidence(horizon, metrics),
                                    "macd_bias": metrics.get("macd_bias"), "features": metrics,
                                }
                                if store.create_eval(sample): pending[pkey] = sample
                    last_error = ""
                except Exception as exc:
                    last_error = str(exc); print(f"[WARN] QMT read error: {exc}")

            if now - last_status >= 10.0:
                status = {"symbols": sorted(symbols), "counts": counts, "eval_counts": eval_counts, "mobile_symbol": mobile_symbol, "pending_eval": len(pending), "session": session, "last_error": last_error}
                try: store.heartbeat(status); _write_status(symbols, counts, eval_counts, last_error)
                except Exception as exc: print(f"[WARN] status write error: {exc}")
                print(f"{datetime.now().strftime('%H:%M:%S')} symbols={len(symbols)} rows={sum(counts.values())} eval60={eval_counts['60']} eval120={eval_counts['120']} mobile={mobile_symbol or '-'}")
                last_status = now
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("Research recorder stopped")
    finally:
        cloud.stop(); uploader.stop()
        for sid in subscriptions.values():
            if sid is not None:
                try: xtdata.unsubscribe_quote(sid)
                except Exception: pass
        if store.conn is not None:
            try: store.conn.commit(); store.conn.close()
            except Exception: pass


if __name__ == "__main__":
    main()
