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
    passed = True

    cfg = load_bridge_config()
    errs = cfg.validation_errors()
    passed &= mark("Persistent configuration", not errs, "; ".join(errs))

    if not errs:
        try:
            CloudBridge(cfg).ping()
            passed &= mark("Supabase REST connection", True, cfg.url)
        except Exception as exc:
            passed &= mark("Supabase REST connection", False, repr(exc))

    try:
        from xtquant import xtdata
        passed &= mark("xtquant import", True)
        try:
            data = xtdata.get_full_tick(["000400.SZ"]) or {}
            row = data.get("000400.SZ") or {}
            passed &= mark("QMT XtData connection", bool(row), f"lastPrice={row.get('lastPrice')}")
        except Exception as exc:
            passed &= mark("QMT XtData connection", False, repr(exc))
    except Exception as exc:
        passed &= mark("xtquant import", False, repr(exc))

    stable = Path(os.environ.get("LOCALAPPDATA", "")) / "AStockQMT"
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "AStockQMTCloudBridge.cmd"
    mark("Stable install folder", stable.exists(), str(stable))
    mark("Windows Startup launcher", startup.exists(), str(startup))

    print("\nRESULT:", "READY" if passed else "NEEDS FIX")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
