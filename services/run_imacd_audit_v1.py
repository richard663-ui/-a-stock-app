# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path

import services.imacd_research_audit_v1 as imacd
import services.qmt_l1_60s_walkforward_v1 as base

FILTER_MODULE = "services.v4r_imacd_filter_audit_v1"
FILTER_FILE = "v4r_imacd_filter_audit_v1.py"
FILTER_MARKER = "v4r-imacd-regime-filter-audit-v1-20260906"
FILTER_URL = "https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/services/v4r_imacd_filter_audit_v1.py"


def _bootstrap_filter_module() -> int:
    """Allow older copies of the single research BAT to pick up the next audit.

    The BAT already downloads this launcher from main on every run.  Therefore
    this launcher can fetch the one missing downstream research module without
    asking the user to replace the BAT yet again.  Production code is untouched.
    """
    service_dir = Path(__file__).resolve().parent
    target = service_dir / FILTER_FILE
    needs_refresh = True
    if target.exists():
        try:
            needs_refresh = FILTER_MARKER not in target.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            needs_refresh = True
    if needs_refresh:
        print("[RESEARCH CHAIN] Fetching latest V4R x iMACD filter audit...")
        tmp = target.with_suffix(".tmp")
        try:
            with urllib.request.urlopen(FILTER_URL, timeout=25) as response:
                text = response.read().decode("utf-8")
            if FILTER_MARKER not in text:
                raise RuntimeError("downloaded filter module failed version marker check")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(target)
        except Exception as exc:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            print(f"[RESEARCH CHAIN FAIL] {type(exc).__name__}: {exc}")
            return 1
    importlib.invalidate_caches()
    try:
        filt = importlib.import_module(FILTER_MODULE)
        return int(filt.main())
    except Exception as exc:
        print(f"[RESEARCH CHAIN FAIL] {type(exc).__name__}: {exc}")
        return 1


def main() -> int:
    root = Path.home() / "AStockData" / "qmt_l1_walkforward_v3"
    runs = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True) if root.exists() else []
    if not runs:
        print("[IMACD FAIL] no V3 walk-forward run directory found")
        return 2
    run = runs[0]
    report_path = run / "walkforward_report_v3.json"
    dataset_root = run / "dataset"
    if not report_path.exists() or not dataset_root.exists():
        print(f"[IMACD FAIL] latest V3 run incomplete: {run}")
        return 2
    v3 = json.loads(report_path.read_text(encoding="utf-8"))
    dates = list(((v3.get("dataset") or {}).get("trade_dates") or []))
    baseline = (((v3.get("aggregates") or {}).get("V4R") or {}).get("logistic_balanced") or {})
    if len(dates) < 6 or not baseline:
        print("[IMACD FAIL] V3 dates/baseline missing")
        return 2
    generated_at = datetime.now(base.CN_TZ).isoformat(timespec="seconds")
    print(f"[IMACD] source_run={run.name} dates={len(dates)}")
    print("[IMACD] fixed 5s+15s 12/26/9 state engine; q90 validation ranking; exact future bid; 2bp hurdle")
    result = imacd.run_audit(dataset_root, dates, baseline, generated_at)
    out = run / "imacd_report_v1.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if result.get("fatal_error"):
        print(f"[IMACD FAIL] {result['fatal_error']}")
        return 1
    primary = (((result.get("variants") or {}).get(imacd.PRIMARY_VARIANT) or {}).get("aggregate") or {})
    gate = result.get("development_gate") or {}
    print("[IMACD RESULT]")
    print(f"  accuracy={primary.get('directional_accuracy_pct')}%")
    print(f"  coverage={primary.get('directional_coverage_pct')}%")
    print(f"  net={primary.get('avg_net_edge_bp')}bp")
    print(f"  n={primary.get('directional_predictions')} positive_days={primary.get('positive_net_edge_days')}")
    print(f"  gate={gate.get('pass')} checks={gate.get('checks')}")
    print(f"  cloud_sync={result.get('cloud_sync')}")
    print("[IMACD RULE] Passing historical development gate can enter shadow only; production still requires unseen prospective days.")

    # Newer BATs run the filter as their own explicit step. Older BATs do not.
    # This compatibility chain means the user's existing BAT can still advance
    # the research program after it downloads this launcher from main.
    if os.environ.get("ASTOCK_SKIP_FILTER_CHAIN", "0") != "1":
        print("[RESEARCH CHAIN] Existing BAT detected; continuing directly into V4R x iMACD filter audit.")
        return _bootstrap_filter_module()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
