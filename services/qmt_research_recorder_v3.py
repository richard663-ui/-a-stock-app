# -*- coding: utf-8 -*-
"""AStock Research Recorder V3.

Integrity goals:
- raw QMT five-level snapshots are stored locally with order-book changes preserved;
- 5-second score traces use the same short-horizon scoring rules as mobile v7;
- forward evaluation uses strictly non-overlapping 60s/120s samples;
- horizons use wall-clock time, not last-trade timestamps;
- late/restarted samples are marked invalid instead of contaminating accuracy;
- scored rows are durably synced to Supabase from local SQLite;
- recorder can start before QMT and keeps retrying subscriptions.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xtquant import xtdata
from modules.cloud_bridge import CloudBridge, load_bridge_config
from modules.qmt_live import normalize_tick
from modules.research_forward_model import high_confidence, score_label, score_rows

RECORDER_VERSION = "research-recorder-v3-20260825"
MODEL_VERSION = "research-shadow-v3-parity-mobile-v7"
PRODUCTION_MODEL = "mobile-selective-v7-macd-context"

PERSIST_DIR = Path.home() / ".a_stock_qmt"
WATCHLIST_PATH = PERSIST_DIR / "research_watchlist.txt"
DATA_ROOT = Path(os.environ.get("ASTOCK_RESEARCH_DATA_DIR", str(Path.home() / "AStockData"))).expanduser()
STATUS_PATH = DATA_ROOT / "recorder_status.json"
LOCK_PATH = PERSIST_DIR / "research_recorder.lock"

POLL_SECONDS = 0.25
TRACE_SECONDS = 5
WATCHLIST_RELOAD_SECONDS = 5.0
MOBILE_SYMBOL_POLL_SECONDS = 6.0
MACD_POLL_SECONDS = 25.0
SUB_RETRY_SECONDS = 10.0
STATUS_SECONDS = 10.0
SYNC_SECONDS = 10.0
MAX_SCORE_DELAY_SECONDS = 3.0
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
            "# The current phone-selected stock is also recorded automatically.\n",
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


def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        x = float(value)
        return x if x == x else default
    except Exception:
        return default


def _i(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return default


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
    if 570 <= m < 690:
        return (690 - m) * 60 - d.second
    if 780 <= m < 900:
        return (900 - m) * 60 - d.second
    return 0


def _tick_ts(row: Dict[str, Any]) -> float:
    raw = _f(row.get("time"), 0.0) or 0.0
    if raw > 1e12:
        return raw / 1000.0
    text = str(row.get("captured_at") or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _coverage_seconds(rows: Iterable[Dict[str, Any]]) -> float:
    a = list(rows)
    if len(a) < 2:
        return 0.0
    start, end = _tick_ts(a[0]), _tick_ts(a[-1])
    return max(0.0, end - start) if start > 0 and end >= start else 0.0


def _snapshot_hash(row: Dict[str, Any]) -> str:
    payload = [
        row.get("symbol"), row.get("captured_at"), row.get("lastPrice"),
        row.get("volume"), row.get("amount"),
        list(row.get("bidPrice") or []), list(row.get("askPrice") or []),
        list(row.get("bidVol") or []), list(row.get("askVol") or []),
    ]
    return hashlib.sha1(_json(payload).encode("utf-8")).hexdigest()


def _acquire_single_instance():
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    fh = LOCK_PATH.open("a+b")
    if os.name == "nt":
        import msvcrt
        try:
            fh.seek(0)
            if fh.tell() == 0 and fh.read(1) == b"":
                fh.write(b"0")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            return None
    return fh


class DailyStore:
    def __init__(self):
        self.day = ""
        self.conn: Optional[sqlite3.Connection] = None
        self.pending_writes = 0
        self.last_commit = 0.0

    def _db_path(self, day: str) -> Path:
        folder = DATA_ROOT / "raw" / day
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "ticks.sqlite3"

    def _open(self, day: str) -> sqlite3.Connection:
        if self.conn is not None:
            try:
                self.conn.commit()
                self.conn.close()
            except Exception:
                pass
        conn = sqlite3.connect(self._db_path(day), timeout=15.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_ticks_v3 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL, captured_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
                qmt_time INTEGER, timetag TEXT, last_price REAL, open REAL, high REAL, low REAL,
                last_close REAL, avg_price REAL, amount REAL, volume INTEGER,
                bid_price_json TEXT NOT NULL, ask_price_json TEXT NOT NULL,
                bid_vol_json TEXT NOT NULL, ask_vol_json TEXT NOT NULL,
                snapshot_hash TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL DEFAULT 'QMT_L1_FIVE_LEVEL'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_v3_symbol_time ON raw_ticks_v3(symbol,recorded_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS score_trace_v3 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL, generated_at TEXT NOT NULL, price REAL NOT NULL,
                score60 INTEGER NOT NULL, score120 INTEGER NOT NULL,
                direction60 TEXT NOT NULL, direction120 TEXT NOT NULL,
                features_json TEXT NOT NULL, model_version TEXT NOT NULL,
                UNIQUE(symbol,generated_at,model_version)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_v3_symbol_time ON score_trace_v3(symbol,generated_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS forward_eval_v3 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL, horizon_seconds INTEGER NOT NULL,
                bucket_start INTEGER NOT NULL, generated_ts REAL NOT NULL, expires_ts REAL NOT NULL,
                generated_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                entry_price REAL NOT NULL, exit_price REAL, return_pct REAL,
                direction TEXT NOT NULL, score INTEGER NOT NULL, score_abs INTEGER NOT NULL,
                tier TEXT NOT NULL, high_confidence INTEGER NOT NULL DEFAULT 0,
                flat INTEGER, correct INTEGER, valid INTEGER NOT NULL DEFAULT 1,
                invalid_reason TEXT, score_delay_seconds REAL, actual_horizon_seconds REAL,
                macd_bias REAL, features_json TEXT NOT NULL, model_version TEXT NOT NULL,
                scored_at TEXT, cloud_synced INTEGER NOT NULL DEFAULT 0,
                UNIQUE(symbol,horizon_seconds,bucket_start,model_version)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_v3_unsynced ON forward_eval_v3(cloud_synced,scored_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_v3_symbol ON forward_eval_v3(symbol,horizon_seconds,generated_ts)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recorder_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        self.day = day
        self.conn = conn
        self.pending_writes = 0
        self.last_commit = time.time()
        return conn

    def ensure(self) -> sqlite3.Connection:
        day = datetime.now().strftime("%Y-%m-%d")
        return self._open(day) if self.conn is None or self.day != day else self.conn

    def _maybe_commit(self):
        if self.conn is None:
            return
        now = time.time()
        if self.pending_writes >= 100 or now - self.last_commit >= 1.0:
            self.conn.commit()
            self.pending_writes = 0
            self.last_commit = now

    def write_tick(self, row: Dict[str, Any]) -> bool:
        conn = self.ensure()
        cur = conn.execute("""
            INSERT OR IGNORE INTO raw_ticks_v3 (
                symbol,captured_at,recorded_at,qmt_time,timetag,last_price,open,high,low,last_close,avg_price,
                amount,volume,bid_price_json,ask_price_json,bid_vol_json,ask_vol_json,snapshot_hash
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            str(row.get("symbol") or ""), str(row.get("captured_at") or ""),
            datetime.now().astimezone().isoformat(timespec="milliseconds"),
            _i(row.get("time")), str(row.get("timetag") or ""), _f(row.get("lastPrice")),
            _f(row.get("open")), _f(row.get("high")), _f(row.get("low")), _f(row.get("lastClose")),
            _f(row.get("avgPrice")), _f(row.get("amount")), _i(row.get("volume")),
            _json(list(row.get("bidPrice") or [])), _json(list(row.get("askPrice") or [])),
            _json(list(row.get("bidVol") or [])), _json(list(row.get("askVol") or [])),
            _snapshot_hash(row),
        ))
        if cur.rowcount > 0:
            self.pending_writes += 1
        self._maybe_commit()
        return cur.rowcount > 0

    def write_trace(self, symbol: str, metrics: Dict[str, Any], wall_ts: float):
        conn = self.ensure()
        d60, d120 = score_label(metrics["score60"]), score_label(metrics["score120"])
        conn.execute("""
            INSERT OR IGNORE INTO score_trace_v3 (
                symbol,generated_at,price,score60,score120,direction60,direction120,features_json,model_version
            ) VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            symbol, datetime.fromtimestamp(wall_ts).astimezone().isoformat(),
            float(metrics["price"]), int(metrics["score60"]), int(metrics["score120"]),
            d60["direction"], d120["direction"], _json(metrics), MODEL_VERSION,
        ))
        self.pending_writes += 1
        self._maybe_commit()

    def create_eval(self, sample: Dict[str, Any]) -> bool:
        conn = self.ensure()
        cur = conn.execute("""
            INSERT OR IGNORE INTO forward_eval_v3 (
                symbol,horizon_seconds,bucket_start,generated_ts,expires_ts,generated_at,expires_at,
                entry_price,direction,score,score_abs,tier,high_confidence,macd_bias,features_json,model_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            sample["symbol"], sample["horizon_seconds"], sample["bucket_start"],
            sample["generated_ts"], sample["expires_ts"], sample["generated_at"], sample["expires_at"],
            sample["entry_price"], sample["direction"], sample["score"], abs(sample["score"]),
            sample["tier"], 1 if sample["high_confidence"] else 0, sample.get("macd_bias"),
            _json(sample["features"]), MODEL_VERSION,
        ))
        if cur.rowcount > 0:
            self.pending_writes += 1
        self._maybe_commit()
        return cur.rowcount > 0

    def finalize_eval(self, sample: Dict[str, Any], exit_price: Optional[float], now_ts: float,
                      valid: bool, invalid_reason: Optional[str] = None) -> Dict[str, Any]:
        entry = float(sample["entry_price"])
        ret = None
        flat = None
        correct = None
        if valid and exit_price is not None and exit_price > 0:
            ret = (float(exit_price) / entry - 1.0) * 100.0
            flat_band = max(0.01, float(sample["features"].get("noise_pct") or 0.02))
            flat = abs(ret) < flat_band
            if not flat and sample["direction"] != "WATCH":
                correct = ret > 0 if sample["direction"] == "UP" else ret < 0
        delay = max(0.0, now_ts - float(sample["expires_ts"]))
        actual_horizon = max(0.0, now_ts - float(sample["generated_ts"]))
        scored_at = datetime.fromtimestamp(now_ts).astimezone().isoformat()
        conn = self.ensure()
        conn.execute("""
            UPDATE forward_eval_v3 SET
                exit_price=?,return_pct=?,flat=?,correct=?,valid=?,invalid_reason=?,
                score_delay_seconds=?,actual_horizon_seconds=?,scored_at=?,cloud_synced=0
            WHERE symbol=? AND horizon_seconds=? AND bucket_start=? AND model_version=?
        """, (
            exit_price, ret, None if flat is None else (1 if flat else 0),
            None if correct is None else (1 if correct else 0),
            1 if valid else 0, invalid_reason, delay, actual_horizon, scored_at,
            sample["symbol"], sample["horizon_seconds"], sample["bucket_start"], MODEL_VERSION,
        ))
        conn.commit()
        return {
            **sample, "exit_price": exit_price, "return_pct": ret, "flat": flat, "correct": correct,
            "valid": valid, "invalid_reason": invalid_reason, "score_delay_seconds": delay,
            "actual_horizon_seconds": actual_horizon, "scored_at": scored_at,
        }

    def load_unfinished(self) -> List[Dict[str, Any]]:
        conn = self.ensure()
        rows = conn.execute("""
            SELECT symbol,horizon_seconds,bucket_start,generated_ts,expires_ts,generated_at,expires_at,
                   entry_price,direction,score,tier,high_confidence,macd_bias,features_json
            FROM forward_eval_v3 WHERE scored_at IS NULL AND model_version=?
        """, (MODEL_VERSION,)).fetchall()
        out = []
        for r in rows:
            out.append({
                "symbol": r[0], "horizon_seconds": int(r[1]), "bucket_start": int(r[2]),
                "generated_ts": float(r[3]), "expires_ts": float(r[4]), "generated_at": r[5],
                "expires_at": r[6], "entry_price": float(r[7]), "direction": r[8], "score": int(r[9]),
                "tier": r[10], "high_confidence": bool(r[11]), "macd_bias": _f(r[12], 0.0) or 0.0,
                "features": json.loads(r[13] or "{}"),
            })
        return out

    def heartbeat(self, payload: Dict[str, Any]):
        conn = self.ensure()
        now_text = datetime.now().astimezone().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO recorder_meta(key,value,updated_at) VALUES('heartbeat',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (_json(payload), now_text),
        )
        conn.commit()
        self.pending_writes = 0
        self.last_commit = time.time()


class CloudState:
    def __init__(self):
        self._lock = threading.Lock()
        self._symbol = ""
        self._macd: Dict[str, Tuple[float, float]] = {}
        self._stop = threading.Event()
        self.bridge: Optional[CloudBridge] = None
        try:
            cfg = load_bridge_config()
            if cfg.ok:
                self.bridge = CloudBridge(cfg, timeout=3.0)
        except Exception:
            self.bridge = None
        threading.Thread(target=self._run, name="research-cloud-state", daemon=True).start()

    def mobile_symbol(self) -> str:
        with self._lock:
            return self._symbol

    def macd_bias(self, symbol: str) -> float:
        with self._lock:
            item = self._macd.get(symbol)
        if not item:
            return 0.0
        bias, updated_ts = item
        return float(bias) if time.time() - updated_ts <= 180.0 else 0.0

    def stop(self):
        self._stop.set()

    def _run(self):
        last_mobile = 0.0
        last_macd = 0.0
        while not self._stop.is_set():
            now = time.time()
            if self.bridge is not None and now - last_mobile >= MOBILE_SYMBOL_POLL_SECONDS:
                try:
                    s = _normalize_symbol(self.bridge.get_requested_symbol())
                    if s:
                        with self._lock:
                            self._symbol = s
                except Exception:
                    pass
                last_mobile = now
            if self.bridge is not None and now - last_macd >= MACD_POLL_SECONDS:
                try:
                    rows = self.bridge._request(
                        "GET", "mobile_macd_cache",
                        params={"bridge_id": f"eq.{self.bridge.config.bridge_id}", "select": "symbol,payload,updated_at"}
                    ) or []
                    values: Dict[str, Tuple[float, float]] = {}
                    for r in rows:
                        symbol = str(r.get("symbol") or "").upper()
                        payload = r.get("payload") or {}
                        updated = str(r.get("updated_at") or "")
                        try:
                            uts = datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()
                        except Exception:
                            uts = 0.0
                        values[symbol] = (float(payload.get("bias_score") or 0.0), uts)
                    with self._lock:
                        self._macd = values
                except Exception:
                    pass
                last_macd = now
            self._stop.wait(1.0)


class DurableUploader:
    """Sync scored local rows. Network failures do not lose evaluation samples."""
    def __init__(self, bridge: Optional[CloudBridge]):
        self.bridge = bridge
        self.stop_event = threading.Event()
        threading.Thread(target=self._run, name="research-durable-uploader", daemon=True).start()

    def stop(self):
        self.stop_event.set()

    def _db_files(self) -> List[Path]:
        raw = DATA_ROOT / "raw"
        if not raw.exists():
            return []
        return sorted(raw.glob("*/ticks.sqlite3"), reverse=True)[:7]

    def _sync_db(self, path: Path):
        if self.bridge is None:
            return
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='forward_eval_v3'"
            ).fetchone()
            if not exists:
                return
            rows = conn.execute("""
                SELECT id,symbol,horizon_seconds,bucket_start,generated_at,expires_at,entry_price,exit_price,
                       return_pct,direction,score,score_abs,tier,high_confidence,flat,correct,valid,invalid_reason,
                       score_delay_seconds,actual_horizon_seconds,macd_bias,features_json,scored_at
                FROM forward_eval_v3
                WHERE cloud_synced=0 AND scored_at IS NOT NULL
                ORDER BY id LIMIT 200
            """).fetchall()
            for r in rows:
                payload = {
                    "bridge_id": self.bridge.config.bridge_id,
                    "symbol": r[1], "horizon_seconds": int(r[2]),
                    "bucket_start": datetime.fromtimestamp(int(r[3])).astimezone().isoformat(),
                    "generated_at": r[4], "expires_at": r[5], "entry_price": r[6], "exit_price": r[7],
                    "return_pct": r[8], "direction": r[9], "score": int(r[10]), "score_abs": int(r[11]),
                    "tier": r[12], "high_confidence": bool(r[13]),
                    "flat": None if r[14] is None else bool(r[14]),
                    "correct": None if r[15] is None else bool(r[15]),
                    "valid": bool(r[16]), "invalid_reason": r[17],
                    "score_delay_seconds": r[18], "actual_horizon_seconds": r[19],
                    "model_version": MODEL_VERSION, "macd_bias": r[20],
                    "features": json.loads(r[21] or "{}"), "scored_at": r[22],
                }
                self.bridge._request(
                    "POST",
                    "forward_eval_samples_v2?on_conflict=bridge_id,symbol,horizon_seconds,bucket_start",
                    json=payload,
                    headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                )
                conn.execute("UPDATE forward_eval_v3 SET cloud_synced=1 WHERE id=?", (r[0],))
                conn.commit()
        finally:
            conn.close()

    def _run(self):
        while not self.stop_event.is_set():
            if self.bridge is not None:
                for path in self._db_files():
                    if self.stop_event.is_set():
                        break
                    try:
                        self._sync_db(path)
                    except Exception as exc:
                        print(f"[WARN] durable sync failed: {exc}")
                        break
            self.stop_event.wait(SYNC_SECONDS)


def _subscribe(symbol: str) -> Optional[int]:
    try:
        return xtdata.subscribe_quote(symbol, period="tick", count=-1)
    except Exception as exc:
        print(f"[WARN] subscribe {symbol}: {exc}")
        return None


def _write_status(symbols: Iterable[str], counts: Dict[str, int], eval_counts: Dict[str, int],
                  pending: Dict[Tuple[str, int], Dict[str, Any]], last_error: str):
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": not bool(last_error),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "recorder_version": RECORDER_VERSION, "research_model": MODEL_VERSION,
        "production_model_reference": PRODUCTION_MODEL,
        "data_root": str(DATA_ROOT), "watchlist_file": str(WATCHLIST_PATH),
        "symbols": sorted(symbols), "rows_written_today": counts,
        "forward_eval_scored_today": eval_counts, "pending_eval": len(pending),
        "last_error": last_error,
    }
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATUS_PATH)


def main():
    lock = _acquire_single_instance()
    if lock is None:
        print("AStock Research Recorder already running")
        raise SystemExit(17)

    print("AStock Research Recorder started")
    print(f"Recorder: {RECORDER_VERSION}")
    print(f"Research model: {MODEL_VERSION}")
    print("Integrity mode: non-overlap + wall-clock horizons + durable cloud sync")
    print(f"Data root: {DATA_ROOT}")

    store = DailyStore()
    cloud = CloudState()
    uploader = DurableUploader(cloud.bridge)

    subscriptions: Dict[str, Optional[int]] = {}
    last_sub_retry: Dict[str, float] = {}
    last_snapshot: Dict[str, str] = {}
    latest_rows: Dict[str, Dict[str, Any]] = {}
    buffers: Dict[str, Deque[Dict[str, Any]]] = {}
    last_trace_bucket: Dict[str, int] = {}
    pending: Dict[Tuple[str, int], Dict[str, Any]] = {}
    counts: Dict[str, int] = {}
    eval_counts = {"60": 0, "120": 0, "invalid": 0}
    fixed: Set[str] = set()

    last_watchlist = 0.0
    last_status = 0.0
    active_session = _session_key()
    current_day = datetime.now().strftime("%Y-%m-%d")
    last_error = ""

    now0 = time.time()
    try:
        for sample in store.load_unfinished():
            key = (sample["symbol"], sample["horizon_seconds"])
            if sample["expires_ts"] > now0 and _session_key(datetime.fromtimestamp(sample["generated_ts"])) == _session_key():
                pending[key] = sample
            else:
                store.finalize_eval(sample, None, now0, False, "recorder_restart_or_gap")
                eval_counts["invalid"] += 1
    except Exception as exc:
        print(f"[WARN] recovery: {exc}")

    try:
        while True:
            loop_ts = time.time()
            now_dt = datetime.now()
            day = now_dt.strftime("%Y-%m-%d")
            session = _session_key(now_dt)

            if day != current_day:
                current_day = day
                counts = {s: 0 for s in counts}
                eval_counts = {"60": 0, "120": 0, "invalid": 0}
                last_snapshot.clear()
                buffers.clear()
                last_trace_bucket.clear()

            if session != active_session:
                for key, sample in list(pending.items()):
                    store.finalize_eval(sample, None, loop_ts, False, "session_boundary")
                    pending.pop(key, None)
                    eval_counts["invalid"] += 1
                buffers.clear()
                last_trace_bucket.clear()
                active_session = session

            if loop_ts - last_watchlist >= WATCHLIST_RELOAD_SECONDS:
                fixed = _load_watchlist()
                last_watchlist = loop_ts

            symbols = set(fixed)
            mobile_symbol = cloud.mobile_symbol()
            if mobile_symbol:
                symbols.add(mobile_symbol)
            symbols = set(sorted(s for s in symbols if s)[:MAX_SYMBOLS])

            for symbol in sorted(symbols):
                if symbol not in subscriptions:
                    subscriptions[symbol] = _subscribe(symbol)
                    last_sub_retry[symbol] = loop_ts
                    counts.setdefault(symbol, 0)
                    buffers.setdefault(symbol, deque(maxlen=BUFFER_ROWS))
                    print(f"[ADD] {symbol}")
                elif subscriptions[symbol] is None and loop_ts - last_sub_retry.get(symbol, 0.0) >= SUB_RETRY_SECONDS:
                    subscriptions[symbol] = _subscribe(symbol)
                    last_sub_retry[symbol] = loop_ts

            for symbol in list(subscriptions):
                if symbol not in symbols:
                    sid = subscriptions.pop(symbol)
                    last_sub_retry.pop(symbol, None)
                    last_snapshot.pop(symbol, None)
                    latest_rows.pop(symbol, None)
                    buffers.pop(symbol, None)
                    if sid is not None:
                        try:
                            xtdata.unsubscribe_quote(sid)
                        except Exception:
                            pass
                    for key in [k for k in pending if k[0] == symbol]:
                        sample = pending.pop(key)
                        store.finalize_eval(sample, None, loop_ts, False, "symbol_removed")
                        eval_counts["invalid"] += 1
                    print(f"[DROP] {symbol}")

            if symbols:
                try:
                    data = xtdata.get_full_tick(sorted(symbols)) or {}
                    for symbol in symbols:
                        tick = data.get(symbol) or {}
                        if not tick:
                            continue
                        row = normalize_tick(symbol, tick)
                        latest_rows[symbol] = row

                        for horizon in (60, 120):
                            key = (symbol, horizon)
                            sample = pending.get(key)
                            if sample is not None and loop_ts >= sample["expires_ts"]:
                                price = _f(row.get("lastPrice"))
                                delay = loop_ts - sample["expires_ts"]
                                valid = bool(price and price > 0 and delay <= MAX_SCORE_DELAY_SECONDS and session != "CLOSED")
                                reason = None if valid else ("late_scoring" if delay > MAX_SCORE_DELAY_SECONDS else "missing_price_or_session")
                                store.finalize_eval(sample, float(price) if price else None, loop_ts, valid, reason)
                                pending.pop(key, None)
                                if valid:
                                    eval_counts[str(horizon)] += 1
                                else:
                                    eval_counts["invalid"] += 1

                        snap = _snapshot_hash(row)
                        if snap != last_snapshot.get(symbol):
                            last_snapshot[symbol] = snap
                            if store.write_tick(row):
                                counts[symbol] = counts.get(symbol, 0) + 1
                            if session != "CLOSED":
                                buffers.setdefault(symbol, deque(maxlen=BUFFER_ROWS)).append(row)

                        if session == "CLOSED":
                            continue
                        buf = buffers.setdefault(symbol, deque(maxlen=BUFFER_ROWS))
                        trace_bucket = int(loop_ts // TRACE_SECONDS)
                        if len(buf) < 20 or trace_bucket == last_trace_bucket.get(symbol):
                            continue

                        metrics = score_rows(list(buf), cloud.macd_bias(symbol))
                        metrics["coverage_seconds"] = _coverage_seconds(buf)
                        metrics["production_model_reference"] = PRODUCTION_MODEL
                        last_trace_bucket[symbol] = trace_bucket
                        store.write_trace(symbol, metrics, loop_ts)

                        for horizon in (60, 120):
                            key = (symbol, horizon)
                            if key in pending:
                                continue
                            if _seconds_to_close(now_dt) <= horizon + 3:
                                continue
                            coverage = float(metrics.get("coverage_seconds") or 0.0)
                            if horizon == 60 and (coverage < 55 or len(buf) < 20):
                                continue
                            if horizon == 120 and (coverage < 125 or len(buf) < 40):
                                continue
                            score = int(metrics["score60"] if horizon == 60 else metrics["score120"])
                            label = score_label(score)
                            generated_ts = loop_ts
                            expires_ts = generated_ts + horizon
                            bucket = int(generated_ts // horizon) * horizon
                            sample = {
                                "symbol": symbol, "horizon_seconds": horizon, "bucket_start": bucket,
                                "generated_ts": generated_ts, "expires_ts": expires_ts,
                                "generated_at": datetime.fromtimestamp(generated_ts).astimezone().isoformat(),
                                "expires_at": datetime.fromtimestamp(expires_ts).astimezone().isoformat(),
                                "entry_price": float(metrics["price"]), "direction": label["direction"],
                                "score": score, "tier": label["tier"],
                                "high_confidence": high_confidence(horizon, metrics),
                                "macd_bias": metrics.get("macd_bias"), "features": dict(metrics),
                            }
                            if store.create_eval(sample):
                                pending[key] = sample
                    last_error = ""
                except Exception as exc:
                    last_error = str(exc)
                    print(f"[WARN] QMT read error: {exc}")

            if loop_ts - last_status >= STATUS_SECONDS:
                try:
                    store.heartbeat({
                        "symbols": sorted(symbols), "counts": counts, "eval_counts": eval_counts,
                        "mobile_symbol": mobile_symbol, "pending_eval": len(pending),
                        "session": session, "last_error": last_error,
                    })
                    _write_status(symbols, counts, eval_counts, pending, last_error)
                except Exception as exc:
                    print(f"[WARN] status write error: {exc}")
                print(
                    f"{datetime.now().strftime('%H:%M:%S')} symbols={len(symbols)} "
                    f"rows={sum(counts.values())} eval60={eval_counts['60']} "
                    f"eval120={eval_counts['120']} invalid={eval_counts['invalid']}"
                )
                last_status = loop_ts

            elapsed = time.time() - loop_ts
            time.sleep(max(0.02, POLL_SECONDS - elapsed))
    except KeyboardInterrupt:
        print("Research recorder stopped")
    finally:
        cloud.stop()
        uploader.stop()
        for sid in subscriptions.values():
            if sid is not None:
                try:
                    xtdata.unsubscribe_quote(sid)
                except Exception:
                    pass
        if store.conn is not None:
            try:
                store.conn.commit()
                store.conn.close()
            except Exception:
                pass
        try:
            lock.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
