# -*- coding: utf-8 -*-
"""One-shot local health doctor for V18 Final."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.cloud_bridge import CloudBridge, load_bridge_config

CN_TZ = ZoneInfo("Asia/Shanghai")


def _mark(name: str, state: str, detail: str = "") -> None:
    print(f"[{state}] {name}" + (f": {detail}" if detail else ""))


def _market_open() -> bool:
    now = datetime.now(CN_TZ)
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return (570 <= minute <= 690) or (780 <= minute <= 900)


def main() -> int:
    failures = 0

    cfg = load_bridge_config()
    errs = cfg.validation_errors()
    if errs:
        _mark("Persistent configuration", "FAIL", "; ".join(errs)); failures += 1
    else:
        _mark("Persistent configuration", "PASS")
        try:
            CloudBridge(cfg).ping()
            _mark("Supabase REST connection", "PASS", cfg.url)
        except Exception as exc:
            _mark("Supabase REST connection", "FAIL", repr(exc)); failures += 1

    try:
        import pandas  # noqa: F401
        import numpy  # noqa: F401
        import requests  # noqa: F401
        import certifi  # noqa: F401
        _mark("Python runtime dependencies", "PASS")
    except Exception as exc:
        _mark("Python runtime dependencies", "FAIL", repr(exc)); failures += 1

    try:
        from xtquant import xtdata
        _mark("xtquant import", "PASS")
        try:
            data = xtdata.get_full_tick(["000400.SZ"]) or {}
            row = data.get("000400.SZ") or {}
            if row:
                _mark("QMT XtData connection", "PASS", f"lastPrice={row.get('lastPrice')}")
            else:
                _mark("QMT XtData connection", "FAIL", "empty snapshot"); failures += 1
        except Exception as exc:
            _mark("QMT XtData connection", "FAIL", repr(exc)); failures += 1

        try:
            from modules.qmt_level2 import QMTLevel2Manager
            manager = QMTLevel2Manager()
            status = manager.switch("000400.SZ")
            available = [k for k, v in status.get("capabilities", {}).items() if v.get("available")]
            manager.stop()
            if available:
                _mark("QMT Level-2 feeds", "PASS", ", ".join(available))
            elif _market_open():
                _mark("QMT Level-2 feeds", "WARN", "no L2 rows during market hours; run capability probe")
            else:
                _mark("QMT Level-2 feeds", "WARN", "market closed; validate during continuous trading")
        except Exception as exc:
            _mark("QMT Level-2 feeds", "WARN", repr(exc))
    except Exception as exc:
        _mark("xtquant import", "FAIL", repr(exc)); failures += 1

    stable = Path(os.environ.get("LOCALAPPDATA", "")) / "AStockQMT"
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "AStockQMTCloudBridge.cmd"
    required = [
        stable / "services" / "qmt_cloud_bridge.py",
        stable / "modules" / "direction_v18.py",
        stable / "modules" / "setup_vwap.py",
        stable / "modules" / "prediction_journal.py",
    ]
    if stable.exists() and all(p.exists() for p in required):
        _mark("Stable V18 runtime", "PASS", str(stable))
    else:
        _mark("Stable V18 runtime", "FAIL", "run install_cloud_bridge_startup.bat"); failures += 1

    if startup.exists():
        _mark("Windows Startup launcher", "PASS", str(startup))
    else:
        _mark("Windows Startup launcher", "FAIL", "launcher missing"); failures += 1

    db = stable / "runtime" / "one_minute_predictions.sqlite3"
    if db.exists():
        _mark("60-second validation database", "PASS", str(db))
    else:
        _mark("60-second validation database", "WARN", "created automatically after V18 bridge starts")

    print("\nRESULT:", "READY" if failures == 0 else f"NEEDS FIX ({failures} failures)")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
