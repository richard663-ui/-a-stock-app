# -*- coding: utf-8 -*-
"""One-shot diagnostic for the local QMT -> Supabase bridge."""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.cloud_bridge import CloudBridge, load_bridge_config


def mark(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f": {detail}" if detail else ""))
    return ok


def main() -> int:
    core_ok = True

    cfg = load_bridge_config()
    errs = cfg.validation_errors()
    core_ok &= mark("Persistent configuration", not errs, "; ".join(errs))

    if not errs:
        try:
            CloudBridge(cfg).ping()
            core_ok &= mark("Supabase REST connection", True, cfg.url)
        except Exception as exc:
            core_ok &= mark("Supabase REST connection", False, repr(exc))

    try:
        from xtquant import xtdata
        core_ok &= mark("xtquant import", True)
        try:
            data = xtdata.get_full_tick(["000400.SZ"]) or {}
            row = data.get("000400.SZ") or {}
            core_ok &= mark("QMT XtData connection", bool(row), f"lastPrice={row.get('lastPrice')}")
        except Exception as exc:
            core_ok &= mark("QMT XtData connection", False, repr(exc))
    except Exception as exc:
        core_ok &= mark("xtquant import", False, repr(exc))

    stable = Path(os.environ.get("LOCALAPPDATA", "")) / "AStockQMT"
    startup = (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        / "AStockQMTCloudBridge.cmd"
    )
    stable_ok = mark("Stable install folder", stable.exists(), str(stable))
    startup_ok = mark("Windows Startup launcher", startup.exists(), str(startup))

    print()
    if core_ok and stable_ok and startup_ok:
        print("RESULT: READY")
        return 0
    if core_ok:
        print("RESULT: CORE READY - STARTUP NOT INSTALLED")
        print("NEXT: double-click install_cloud_bridge_startup.bat once, then rerun this check.")
        return 1

    print("RESULT: NEEDS FIX")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
