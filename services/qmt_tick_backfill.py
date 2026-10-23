"""Backfill missing sessions from QMT's historical L1 tick service (after close only).

QMT serves 3-second, 5-level historical snapshots for recent sessions. This tool
recovers days the PC missed WITHOUT mixing them into the live archive:

* raw snapshots -> ``<data>/backfill/qmt_history/<day>/ticks.sqlite3`` (table
  ``raw_ticks_v3``, same schema as the live research recorder, source
  ``QMT_HISTORY_DOWNLOAD``)
* optional training-format replay samples -> ``<data>/backfill/training_replay``
  (recorder_version ``qmt-history-replay-l1-v1``; built with the same past-only
  feature code as the walk-forward replay). Live training data is never touched;
  trainers use replay data only when explicitly pointed at it.

Days QMT does not return stay missing. Nothing is interpolated.

    python services/qmt_tick_backfill.py --start 2026-09-08 --end 2026-10-22 --build-samples
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.market_clock import SHANGHAI, market_now
from modules.paths import data_root as default_data_root

BACKFILL_VERSION = "qmt-tick-backfill-v1-20261023"
SOURCE = "QMT_HISTORY_DOWNLOAD"
BENCHMARKS = ["000300.SH", "399006.SZ"]
RAW_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_ticks_v3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, captured_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
    qmt_time INTEGER, timetag TEXT, last_price REAL, open REAL, high REAL, low REAL,
    last_close REAL, avg_price REAL, amount REAL, volume INTEGER,
    bid_price_json TEXT NOT NULL, ask_price_json TEXT NOT NULL,
    bid_vol_json TEXT NOT NULL, ask_vol_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL DEFAULT 'QMT_L1_FIVE_LEVEL'
)"""


def in_market_hours(now: datetime | None = None) -> bool:
    now = market_now(now)
    minute = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 5 <= minute <= 15 * 60 + 5


def _listify(value: Any) -> List[float]:
    try:
        return [float(x) for x in list(value)]
    except TypeError:
        return []


def _store_frame(root: Path, symbol: str, frame) -> Dict[str, int]:
    """Write one symbol's downloaded ticks into per-day backfill databases."""
    import pandas as pd
    counts: Dict[str, int] = {}
    if frame is None or len(frame) == 0:
        return counts
    times = pd.to_numeric(frame["time"], errors="coerce")
    days = pd.to_datetime(times, unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d")
    downloaded = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    for day, part in frame.groupby(days.values):
        folder = root / "backfill" / "qmt_history" / str(day)
        folder.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(folder / "ticks.sqlite3")
        try:
            conn.execute(RAW_SCHEMA)
            rows = []
            for _, r in part.iterrows():
                t = int(r["time"])
                bp, ap = _listify(r.get("bidPrice")), _listify(r.get("askPrice"))
                bv, av = _listify(r.get("bidVol")), _listify(r.get("askVol"))
                payload = json.dumps([symbol, t, r.get("lastPrice"), r.get("volume"), bp, ap, bv, av], default=str)
                rows.append((symbol, datetime.fromtimestamp(t / 1000, SHANGHAI).isoformat(timespec="milliseconds"),
                             downloaded, t, str(r.get("timetag", "")), float(r.get("lastPrice") or 0),
                             float(r.get("open") or 0), float(r.get("high") or 0), float(r.get("low") or 0),
                             float(r.get("lastClose") or 0), None, float(r.get("amount") or 0), int(r.get("volume") or 0),
                             json.dumps(bp), json.dumps(ap), json.dumps(bv), json.dumps(av),
                             hashlib.sha1(payload.encode()).hexdigest(), SOURCE))
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO raw_ticks_v3 (symbol,captured_at,recorded_at,qmt_time,timetag,"
                             "last_price,open,high,low,last_close,avg_price,amount,volume,bid_price_json,ask_price_json,"
                             "bid_vol_json,ask_vol_json,snapshot_hash,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             rows)
            conn.commit()
            counts[str(day)] = conn.total_changes - before
        finally:
            conn.close()
    return counts


def worker(request_path: str, response_path: str) -> None:
    """Runs in a bounded subprocess: downloads one symbol and stores it."""
    import contextlib
    import io
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    result: Dict[str, Any] = {"symbol": request["symbol"], "stored": {}, "error": ""}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            from xtquant import xtdata
            xtdata.enable_hello = False
            symbol, start, end = request["symbol"], request["start"], request["end"]
            xtdata.download_history_data(symbol, "tick", start, end)
            data = xtdata.get_market_data_ex(field_list=[], stock_list=[symbol], period="tick", start_time=start,
                                             end_time=end, count=-1, dividend_type="none", fill_data=False) or {}
        frame = data.get(request["symbol"])
        result["rows"] = 0 if frame is None else int(len(frame))
        result["stored"] = _store_frame(Path(request["data_root"]), request["symbol"], frame)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]
    Path(response_path).write_text(json.dumps(result), encoding="utf-8")


def download_symbol(root: Path, symbol: str, start: date, end: date, timeout: float) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="astock-backfill-") as tmp:
        req, resp = Path(tmp) / "req.json", Path(tmp) / "resp.json"
        req.write_text(json.dumps({"symbol": symbol, "start": start.strftime("%Y%m%d") + "091500",
                                   "end": end.strftime("%Y%m%d") + "150500", "data_root": str(root)}), encoding="utf-8")
        code = (f"import sys; sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
                f"from services.qmt_tick_backfill import worker; worker({str(req)!r}, {str(resp)!r})")
        try:
            subprocess.run([sys.executable, "-c", code], timeout=timeout, capture_output=True, cwd=str(PROJECT_ROOT))
            return json.loads(resp.read_text(encoding="utf-8"))
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            return {"symbol": symbol, "stored": {}, "error": f"{type(exc).__name__}"}


def load_backfill_frame(root: Path, symbol: str, days: List[str]):
    """Rebuild a QMT-like tick frame for the replay feature code."""
    import pandas as pd
    records = []
    for day in days:
        path = root / "backfill" / "qmt_history" / day / "ticks.sqlite3"
        if not path.exists():
            continue
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            for row in conn.execute("SELECT qmt_time, last_price, volume, amount, last_close, bid_price_json, "
                                    "ask_price_json, bid_vol_json, ask_vol_json FROM raw_ticks_v3 WHERE symbol=? "
                                    "ORDER BY qmt_time", (symbol,)):
                records.append({"time": row[0], "lastPrice": row[1], "volume": row[2], "amount": row[3],
                                "lastClose": row[4], "bidPrice": json.loads(row[5]), "askPrice": json.loads(row[6]),
                                "bidVol": json.loads(row[7]), "askVol": json.loads(row[8])})
        finally:
            conn.close()
    return pd.DataFrame(records)


def build_replay_samples(root: Path, stocks: List[str], days: List[str]) -> Dict[str, Any]:
    """Training-format samples from backfilled ticks, using the walk-forward replay code."""
    import services.qmt_l1_60s_walkforward_v1 as replay
    out_root = root / "backfill" / "training_replay"
    raw = {s: replay._normalize_raw(load_backfill_frame(root, s, days), s) for s in stocks + BENCHMARKS}
    available = [d for d in days if all(not f.empty and d in set(f["trade_date"]) for f in raw.values())]
    if not available:
        return {"days": [], "rows": 0, "note": "no day has every stock and both benchmarks"}

    class _Offline:  # replay._build_dataset calls _download(xtdata, ...); serve the archive instead
        pass

    original = replay._download
    try:
        replay._download = lambda _x, s, _a, _b: raw[s]
        replay.MIN_HISTORY_DAYS = 1
        summary = replay._build_dataset(_Offline(), stocks, available[0].replace("-", ""),
                                        available[-1].replace("-", ""), out_root)
    finally:
        replay._download = original
    summary["note"] = "Replay samples: separate root, recorder_version " + replay.RECORDER_VERSION
    return summary


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QMT historical tick backfill (after close only)")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--data-root", type=Path, default=default_data_root())
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--build-samples", action="store_true")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--skip-download", action="store_true",
                        help="rebuild samples from days already in the backfill store")
    args = parser.parse_args(argv)
    if in_market_hours():
        print("Refusing to download history during market hours; it competes with live collection.")
        return 2
    cfg = json.loads((PROJECT_ROOT / "configs" / "collection.json").read_text(encoding="utf-8"))
    stocks = [s.upper() for s in (args.symbols or cfg["basket"])]
    results = []
    if args.skip_download:
        store = args.data_root / "backfill" / "qmt_history"
        days = sorted(p.name for p in store.glob("????-??-??")
                      if args.start.isoformat() <= p.name <= args.end.isoformat())
    else:
        for symbol in stocks + BENCHMARKS:
            result = download_symbol(args.data_root, symbol, args.start, args.end, args.timeout)
            results.append(result)
            print(f"{symbol}: rows={result.get('rows')} days={len(result.get('stored', {}))} error={result.get('error') or '-'}")
        days = sorted({d for r in results for d in r.get("stored", {})})
    report: Dict[str, Any] = {"version": BACKFILL_VERSION, "generated_at": market_now().isoformat(timespec="seconds"),
                              "requested": [args.start.isoformat(), args.end.isoformat()], "source": SOURCE,
                              "days_returned": days, "symbols": results}
    if args.build_samples and days:
        report["replay_samples"] = build_replay_samples(args.data_root, stocks, days)
        print(json.dumps(report["replay_samples"], default=str)[:400])
    out = args.data_root / "backfill" / "qmt_history" / "backfill_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, out)
    print(f"QMT returned {len(days)} day(s): {', '.join(days) or 'none'}. Report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
