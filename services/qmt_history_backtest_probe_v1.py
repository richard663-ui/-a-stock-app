# -*- coding: utf-8 -*-
"""Probe whether the connected QMT runtime can support historical L1/tick replay.

This is research-only and does not place orders or stop any existing recorder.
It checks:
- xtquant availability;
- xtquant.qmttools.run_strategy_file availability;
- historical tick download/read for a small symbol set;
- five-level bid/ask coverage and date span;
- whether the data is rich enough to build the AStock 60s historical replay.

A compact JSON report is saved locally and, when the existing cloud bridge is
configured, synced into ml_training_reports_v1 with scope QMT_HISTORY_PROBE.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

PROBE_VERSION = "qmt-history-backtest-probe-v1-20260904"
DEFAULT_SYMBOLS = ["301236.SZ", "000400.SZ"]
OUT_DIR = Path.home() / "AStockData" / "qmt_history_probe"


def _arr_len(v: Any) -> int:
    if isinstance(v, np.ndarray):
        return int(v.size)
    if isinstance(v, (list, tuple)):
        return len(v)
    return 0


def _first_positive(v: Any) -> Optional[float]:
    try:
        if isinstance(v, np.ndarray):
            if not v.size:
                return None
            x = float(v.flat[0])
        elif isinstance(v, (list, tuple)):
            if not v:
                return None
            x = float(v[0])
        else:
            return None
        return x if math.isfinite(x) and x > 0 else None
    except Exception:
        return None


def _to_frame(obj: Any) -> pd.DataFrame:
    if obj is None:
        return pd.DataFrame()
    if isinstance(obj, pd.DataFrame):
        return obj.copy()
    if isinstance(obj, np.ndarray):
        try:
            return pd.DataFrame.from_records(obj)
        except Exception:
            try:
                return pd.DataFrame(obj)
            except Exception:
                return pd.DataFrame()
    if isinstance(obj, list):
        try:
            return pd.DataFrame(obj)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def _timestamps(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype="datetime64[ns]")
    if "time" in frame.columns:
        raw = pd.to_numeric(frame["time"], errors="coerce")
        # QMT tick timestamps are normally milliseconds since epoch.
        out = pd.to_datetime(raw, unit="ms", errors="coerce")
        if out.notna().any():
            return out
    if "stime" in frame.columns:
        out = pd.to_datetime(frame["stime"], errors="coerce")
        if out.notna().any():
            return out
    try:
        return pd.to_datetime(frame.index, errors="coerce")
    except Exception:
        return pd.Series(pd.NaT, index=frame.index)


def _coverage(frame: pd.DataFrame, col: str, min_levels: int = 1) -> float:
    if frame.empty or col not in frame.columns:
        return 0.0
    vals = frame[col].tolist()
    return 100.0 * sum(_arr_len(v) >= min_levels for v in vals) / len(vals)


def _positive_top_coverage(frame: pd.DataFrame, bid_col: str, ask_col: str) -> float:
    if frame.empty or bid_col not in frame.columns or ask_col not in frame.columns:
        return 0.0
    good = 0
    for b, a in zip(frame[bid_col].tolist(), frame[ask_col].tolist()):
        bp, ap = _first_positive(b), _first_positive(a)
        if bp is not None and ap is not None and ap >= bp:
            good += 1
    return 100.0 * good / len(frame)


def _probe_symbol(xtdata: Any, symbol: str, start: str, end: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"symbol": symbol, "download_ok": False, "read_ok": False}
    t0 = time.time()
    try:
        xtdata.download_history_data(symbol, "tick", start, end)
        out["download_ok"] = True
    except Exception as exc:
        out["download_error"] = f"{type(exc).__name__}: {exc}"
    out["download_seconds"] = round(time.time() - t0, 3)

    try:
        data = xtdata.get_market_data_ex(
            field_list=[], stock_list=[symbol], period="tick",
            start_time=start, end_time=end, count=-1,
            dividend_type="none", fill_data=False,
        ) or {}
        frame = _to_frame(data.get(symbol))
        out["read_ok"] = not frame.empty
        out["rows"] = int(len(frame))
        out["fields"] = [str(x) for x in frame.columns.tolist()]
        if frame.empty:
            out["read_error"] = "empty_tick_history"
            return out

        ts = _timestamps(frame)
        valid_ts = ts.dropna()
        dates = sorted({x.date().isoformat() for x in valid_ts.tolist()})
        out["trade_dates"] = dates
        out["trade_day_count"] = len(dates)
        out["first_time"] = valid_ts.min().isoformat() if not valid_ts.empty else None
        out["last_time"] = valid_ts.max().isoformat() if not valid_ts.empty else None
        out["bid_levels_5_coverage_pct"] = round(_coverage(frame, "bidPrice", 5), 3)
        out["ask_levels_5_coverage_pct"] = round(_coverage(frame, "askPrice", 5), 3)
        out["bidvol_levels_5_coverage_pct"] = round(_coverage(frame, "bidVol", 5), 3)
        out["askvol_levels_5_coverage_pct"] = round(_coverage(frame, "askVol", 5), 3)
        out["valid_top_book_pct"] = round(_positive_top_coverage(frame, "bidPrice", "askPrice"), 3)
        out["has_volume"] = "volume" in frame.columns
        out["has_amount"] = "amount" in frame.columns
        out["has_last_price"] = "lastPrice" in frame.columns
        out["history_replay_ready"] = bool(
            len(frame) >= 500
            and len(dates) >= 2
            and out["valid_top_book_pct"] >= 70.0
            and out["bid_levels_5_coverage_pct"] >= 50.0
            and out["ask_levels_5_coverage_pct"] >= 50.0
        )
    except Exception as exc:
        out["read_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _sync_cloud(report: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from modules.cloud_bridge import CloudBridge, load_bridge_config
        cfg = load_bridge_config()
        bridge = CloudBridge(cfg, timeout=12.0)
        generated_at = str(report["generated_at"])
        payload = {
            "bridge_id": cfg.bridge_id,
            "scope": "QMT_HISTORY_PROBE",
            "trainer_version": PROBE_VERSION,
            "generated_at": generated_at,
            "maturity": "PROBE",
            "protocol": "QMT_LOCAL_TICK_HISTORY_CAPABILITY",
            "samples_total": int(sum(int(x.get("rows") or 0) for x in report.get("symbols", []))),
            "samples_test_nonoverlap": 0,
            "report": report,
        }
        bridge._request(
            "POST",
            "ml_training_reports_v1?on_conflict=bridge_id,scope,generated_at",
            json=payload,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        return {"ok": True, "scope": payload["scope"]}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--start", default="")
    p.add_argument("--end", default="")
    p.add_argument("--force-market-hours", action="store_true")
    a = p.parse_args()

    now = datetime.now()
    minute = now.hour * 60 + now.minute
    market_open = now.weekday() < 5 and ((570 <= minute < 690) or (780 <= minute < 900))
    if market_open and not a.force_market_hours:
        print("[STOP] Historical downloads are intentionally blocked during market hours.")
        print("Run after 15:05 or use --force-market-hours only if you accept possible QMT slowdown.")
        return 3

    end_d = date.today() if not a.end else datetime.strptime(a.end, "%Y%m%d").date()
    start_d = (end_d - timedelta(days=max(2, int(a.days)))) if not a.start else datetime.strptime(a.start, "%Y%m%d").date()
    start, end = start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d")
    symbols = [x.strip().upper() for x in str(a.symbols).split(",") if x.strip()]

    report: Dict[str, Any] = {
        "probe_version": PROBE_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "start": start, "end": end, "requested_days": int(a.days),
        "requested_symbols": symbols,
        "market_open_at_start": market_open,
        "orders_placed": False,
        "runtime_stopped": False,
        "symbols": [],
    }

    try:
        import xtquant
        from xtquant import xtdata
        report["xtquant_import_ok"] = True
        report["xtquant_module"] = str(getattr(xtquant, "__file__", ""))
        report["xtdata_data_dir"] = str(getattr(xtdata, "data_dir", ""))
    except Exception as exc:
        report["xtquant_import_ok"] = False
        report["xtquant_error"] = f"{type(exc).__name__}: {exc}"
        print(f"[FAIL] xtquant import: {report['xtquant_error']}")
        return 2

    try:
        from xtquant.qmttools import run_strategy_file  # noqa: F401
        report["qmttools_backtest_api"] = True
        print("[PASS] xtquant.qmttools.run_strategy_file is available.")
    except Exception as exc:
        report["qmttools_backtest_api"] = False
        report["qmttools_error"] = f"{type(exc).__name__}: {exc}"
        print(f"[WARN] qmttools backtest API unavailable: {report['qmttools_error']}")

    print(f"[INFO] QMT historical tick probe {start} -> {end}")
    for symbol in symbols:
        print(f"[PROBE] {symbol} downloading/reading tick history...")
        item = _probe_symbol(xtdata, symbol, start, end)
        report["symbols"].append(item)
        if item.get("history_replay_ready"):
            print(
                f"[PASS] {symbol}: rows={item.get('rows')} days={item.get('trade_day_count')} "
                f"topbook={item.get('valid_top_book_pct')}% five-level-bid={item.get('bid_levels_5_coverage_pct')}%"
            )
        else:
            print(
                f"[WARN] {symbol}: rows={item.get('rows', 0)} days={item.get('trade_day_count', 0)} "
                f"error={item.get('read_error') or item.get('download_error') or 'insufficient_history/book'}"
            )

    ready = [x for x in report["symbols"] if x.get("history_replay_ready")]
    report["history_replay_ready_symbols"] = [x["symbol"] for x in ready]
    report["history_replay_ready"] = bool(ready)
    report["recommended_next_step"] = (
        "BUILD_QMT_L1_60S_HISTORICAL_REPLAY" if ready
        else "FIX_OR_DOWNLOAD_QMT_TICK_HISTORY_FIRST"
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "latest.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["cloud_sync"] = _sync_cloud(report)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[REPORT] {path}")
    if report["cloud_sync"].get("ok"):
        print("[PASS] Probe report synced to cloud scope QMT_HISTORY_PROBE.")
    else:
        print(f"[WARN] Cloud sync failed; local report kept: {report['cloud_sync'].get('error')}")

    if report["history_replay_ready"]:
        print("[READY] QMT historical L1/tick replay is feasible on this runtime.")
        print("[NEXT] Build multi-day 60s walk-forward replay from historical tick; do not tune on the final test day.")
        return 0
    print("[NOT READY] Historical tick/five-level data is not sufficient yet.")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
