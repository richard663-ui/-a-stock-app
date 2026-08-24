# -*- coding: utf-8 -*-
"""Persistent QMT research recorder for future 60s/120s quant research.

Design goals:
- Independent from the mobile UI and prediction bridge.
- Save raw QMT tick snapshots first; derived factors can be rebuilt later.
- Keep collecting a fixed research watchlist even when the phone switches stocks.
- Also follow the current mobile-watched symbol when cloud access is available.
- Never upload the research database to GitHub/Supabase.

Default storage:
    %USERPROFILE%\AStockData\raw\YYYY-MM-DD\ticks.sqlite3
Persistent watchlist:
    %USERPROFILE%\.a_stock_qmt\research_watchlist.txt

One symbol per line. Accepted forms: 000400, 000400.SZ, 600522.SH.
Lines starting with # are ignored.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xtquant import xtdata

from modules.cloud_bridge import CloudBridge, load_bridge_config
from modules.qmt_live import normalize_tick

PERSIST_DIR = Path.home() / ".a_stock_qmt"
WATCHLIST_PATH = PERSIST_DIR / "research_watchlist.txt"
DATA_ROOT = Path(os.environ.get("ASTOCK_RESEARCH_DATA_DIR", str(Path.home() / "AStockData"))).expanduser()
STATUS_PATH = DATA_ROOT / "recorder_status.json"
POLL_SECONDS = 0.25
WATCHLIST_RELOAD_SECONDS = 5.0
MOBILE_SYMBOL_POLL_SECONDS = 6.0
DB_COMMIT_SECONDS = 1.0
MAX_SYMBOLS = 60


def _normalize_symbol(value: str) -> str:
    text = str(value or "").strip().upper()
    if not text or text.startswith("#"):
        return ""
    if "." in text:
        code, market = text.split(".", 1)
        if len(code) == 6 and code.isdigit() and market in {"SZ", "SH", "BJ"}:
            return f"{code}.{market}"
        return ""
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
            "# The recorder also follows the stock currently selected on the phone.\n",
            encoding="utf-8",
        )
        return set()
    out: Set[str] = set()
    for line in WATCHLIST_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        symbol = _normalize_symbol(line)
        if symbol:
            out.add(symbol)
    return out


def _json_list(value) -> str:
    return json.dumps(list(value or []), ensure_ascii=False, separators=(",", ":"))


def _as_float(value):
    try:
        x = float(value)
        return x if x == x else None
    except Exception:
        return None


def _as_int(value):
    try:
        return int(value)
    except Exception:
        return None


@dataclass
class DailyStore:
    day: str = ""
    conn: Optional[sqlite3.Connection] = None
    pending: int = 0
    last_commit: float = 0.0

    def _path_for_day(self, day: str) -> Path:
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
        path = self._path_for_day(day)
        conn = sqlite3.connect(path, timeout=15.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                qmt_time INTEGER,
                timetag TEXT,
                last_price REAL,
                open REAL,
                high REAL,
                low REAL,
                last_close REAL,
                avg_price REAL,
                amount REAL,
                volume INTEGER,
                bid_price_json TEXT NOT NULL,
                ask_price_json TEXT NOT NULL,
                bid_vol_json TEXT NOT NULL,
                ask_vol_json TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'QMT_L1_FIVE_LEVEL',
                recorded_at TEXT NOT NULL,
                UNIQUE(symbol, captured_at, last_price, volume, amount)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_ticks_symbol_time ON raw_ticks(symbol, captured_at)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recorder_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        self.day = day
        self.conn = conn
        self.pending = 0
        self.last_commit = time.time()
        return conn

    def ensure(self) -> sqlite3.Connection:
        day = datetime.now().strftime("%Y-%m-%d")
        if self.conn is None or self.day != day:
            return self._open(day)
        return self.conn

    def write_tick(self, row: Dict) -> bool:
        conn = self.ensure()
        values = (
            str(row.get("symbol") or ""),
            str(row.get("captured_at") or ""),
            _as_int(row.get("time")),
            str(row.get("timetag") or ""),
            _as_float(row.get("lastPrice")),
            _as_float(row.get("open")),
            _as_float(row.get("high")),
            _as_float(row.get("low")),
            _as_float(row.get("lastClose")),
            _as_float(row.get("avgPrice")),
            _as_float(row.get("amount")),
            _as_int(row.get("volume")),
            _json_list(row.get("bidPrice")),
            _json_list(row.get("askPrice")),
            _json_list(row.get("bidVol")),
            _json_list(row.get("askVol")),
            datetime.now().isoformat(timespec="milliseconds"),
        )
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO raw_ticks (
                symbol,captured_at,qmt_time,timetag,last_price,open,high,low,last_close,avg_price,
                amount,volume,bid_price_json,ask_price_json,bid_vol_json,ask_vol_json,recorded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            values,
        )
        inserted = cur.rowcount > 0
        if inserted:
            self.pending += 1
        now = time.time()
        if self.pending >= 100 or now - self.last_commit >= DB_COMMIT_SECONDS:
            conn.commit()
            self.pending = 0
            self.last_commit = now
        return inserted

    def heartbeat(self, payload: Dict) -> None:
        conn = self.ensure()
        now_text = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO recorder_meta(key,value,updated_at) VALUES('heartbeat',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), now_text),
        )
        conn.commit()
        self.pending = 0
        self.last_commit = time.time()


class MobileSymbolPoller:
    def __init__(self):
        self._lock = threading.Lock()
        self._symbol = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="research-mobile-symbol", daemon=True)
        self._thread.start()

    def get(self) -> str:
        with self._lock:
            return self._symbol

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        bridge = None
        try:
            config = load_bridge_config()
            if config.ok:
                bridge = CloudBridge(config, timeout=3.0)
        except Exception:
            bridge = None
        while not self._stop.is_set():
            if bridge is not None:
                try:
                    symbol = _normalize_symbol(bridge.get_requested_symbol())
                    if symbol:
                        with self._lock:
                            self._symbol = symbol
                except Exception:
                    pass
            self._stop.wait(MOBILE_SYMBOL_POLL_SECONDS)


def _subscribe(symbol: str) -> Optional[int]:
    try:
        return xtdata.subscribe_quote(symbol, period="tick", count=-1)
    except Exception as exc:
        print(f"[WARN] subscribe {symbol}: {exc}")
        return None


def _write_status(symbols: Iterable[str], counts: Dict[str, int], last_error: str = "") -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": not bool(last_error),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "data_root": str(DATA_ROOT),
        "watchlist_file": str(WATCHLIST_PATH),
        "symbols": sorted(symbols),
        "rows_written_today": counts,
        "last_error": last_error,
    }
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATUS_PATH)


def main() -> None:
    print("AStock Research Recorder started")
    print("Raw source: QMT five-level snapshot ticks")
    print(f"Data root: {DATA_ROOT}")
    print(f"Watchlist: {WATCHLIST_PATH}")
    print("This recorder is independent from the mobile prediction bridge.")

    store = DailyStore()
    mobile = MobileSymbolPoller()
    subscriptions: Dict[str, Optional[int]] = {}
    last_keys: Dict[str, Tuple] = {}
    counts: Dict[str, int] = {}
    fixed: Set[str] = set()
    last_watchlist_load = 0.0
    last_status = 0.0
    last_error = ""

    try:
        while True:
            now = time.time()
            if now - last_watchlist_load >= WATCHLIST_RELOAD_SECONDS:
                fixed = _load_watchlist()
                last_watchlist_load = now

            symbols = set(fixed)
            mobile_symbol = mobile.get()
            if mobile_symbol:
                symbols.add(mobile_symbol)
            symbols = {s for s in symbols if s}
            if len(symbols) > MAX_SYMBOLS:
                symbols = set(sorted(symbols)[:MAX_SYMBOLS])

            for symbol in sorted(symbols):
                if symbol not in subscriptions:
                    subscriptions[symbol] = _subscribe(symbol)
                    counts.setdefault(symbol, 0)
                    print(f"[ADD] {symbol}")

            # Unsubscribe only symbols that disappeared from both fixed list and mobile watch.
            for symbol in list(subscriptions):
                if symbol not in symbols:
                    sid = subscriptions.pop(symbol)
                    last_keys.pop(symbol, None)
                    if sid is not None:
                        try:
                            xtdata.unsubscribe_quote(sid)
                        except Exception:
                            pass
                    print(f"[DROP] {symbol}")

            if symbols:
                try:
                    data = xtdata.get_full_tick(sorted(symbols)) or {}
                    for symbol in symbols:
                        tick = data.get(symbol) or {}
                        if not tick:
                            continue
                        row = normalize_tick(symbol, tick)
                        key = (
                            row.get("captured_at"),
                            row.get("lastPrice"),
                            row.get("volume"),
                            row.get("amount"),
                            tuple(row.get("bidPrice") or []),
                            tuple(row.get("askPrice") or []),
                            tuple(row.get("bidVol") or []),
                            tuple(row.get("askVol") or []),
                        )
                        if key == last_keys.get(symbol):
                            continue
                        last_keys[symbol] = key
                        if store.write_tick(row):
                            counts[symbol] = counts.get(symbol, 0) + 1
                    last_error = ""
                except Exception as exc:
                    last_error = str(exc)
                    print(f"[WARN] QMT read error: {exc}")

            if now - last_status >= 10.0:
                status = {
                    "symbols": sorted(symbols),
                    "counts": counts,
                    "mobile_symbol": mobile_symbol,
                    "last_error": last_error,
                }
                try:
                    store.heartbeat(status)
                    _write_status(symbols, counts, last_error)
                except Exception as exc:
                    print(f"[WARN] status write error: {exc}")
                total = sum(counts.values())
                print(
                    f"{datetime.now().strftime('%H:%M:%S')} symbols={len(symbols)} "
                    f"rows_today={total} mobile={mobile_symbol or '-'}"
                )
                last_status = now

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("Research recorder stopped")
    finally:
        mobile.stop()
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


if __name__ == "__main__":
    main()
