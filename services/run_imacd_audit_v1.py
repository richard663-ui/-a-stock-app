# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import services.imacd_research_audit_v1 as imacd
import services.qmt_l1_60s_walkforward_v1 as base

CHAIN = (
    (
        "services.v4r_imacd_filter_audit_v1", "v4r_imacd_filter_audit_v1.py",
        "v4r-imacd-regime-filter-audit-v1-20260906",
        "https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/services/v4r_imacd_filter_audit_v1.py",
        "V4R x iMACD filter",
    ),
    (
        "services.v4r_meta_label_audit_v1", "v4r_meta_label_audit_v1.py",
        "v4r-meta-label-exec-audit-v1-20260906",
        "https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/services/v4r_meta_label_audit_v1.py",
        "V4R meta-label",
    ),
    (
        "services.v4r_cross_sectional_rank_audit_v1", "v4r_cross_sectional_rank_audit_v1.py",
        "v4r-cross-sectional-rank-audit-v1-20260906",
        "https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/services/v4r_cross_sectional_rank_audit_v1.py",
        "V4R cross-sectional ranking",
    ),
)


def _find_latest_complete_v3(root: Path) -> Optional[Tuple[Path, Dict[str, Any], Path]]:
    """Return newest COMPLETE V3 run; ignore half-created market-hour folders."""
    if not root.exists():
        return None
    runs = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    for run in runs:
        report_path = run / "walkforward_report_v3.json"
        dataset_root = run / "dataset"
        if not report_path.is_file() or not dataset_root.is_dir():
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        dates = list(((report.get("dataset") or {}).get("trade_dates") or []))
        baseline = (((report.get("aggregates") or {}).get("V4R") or {}).get("logistic_balanced") or {})
        if len(dates) >= 6 and baseline and not report.get("fatal_error"):
            return run, report, dataset_root
    return None


def _bootstrap_module(module_name: str, filename: str, marker: str, url: str, label: str,
                      dataset_root: Path, dates: List[str]) -> int:
    """Fetch downstream audit and force it to reuse the already selected complete dataset."""
    service_dir = Path(__file__).resolve().parent
    target = service_dir / filename
    needs_refresh = True
    if target.exists():
        try:
            needs_refresh = marker not in target.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            needs_refresh = True
    if needs_refresh:
        print(f"[RESEARCH CHAIN] Fetching latest {label} audit...")
        tmp = target.with_suffix(".tmp")
        try:
            with urllib.request.urlopen(url, timeout=25) as response:
                text = response.read().decode("utf-8")
            if marker not in text:
                raise RuntimeError(f"downloaded {label} module failed version marker check")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(target)
        except Exception as exc:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            print(f"[RESEARCH CHAIN FAIL] {label}: {type(exc).__name__}: {exc}")
            return 1
    importlib.invalidate_caches()
    try:
        mod = importlib.import_module(module_name)
        run_fn = getattr(mod, "run", None)
        if callable(run_fn):
            report = run_fn(dataset_root, list(dates))
            if not isinstance(report, dict):
                raise RuntimeError(f"{label} run() did not return a report")
            if "cloud_sync" not in report and callable(getattr(mod, "_sync", None)):
                report["cloud_sync"] = mod._sync(report)
            out_root = getattr(mod, "OUT_ROOT", None)
            if out_root is not None:
                Path(out_root).mkdir(parents=True, exist_ok=True)
                (Path(out_root) / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            cand = report.get("candidate") or {}
            gate = (report.get("development_gate") or {}).get("pass")
            print(f"[RESEARCH CHAIN RESULT] {label}: acc={cand.get('directional_accuracy_pct')} cov={cand.get('directional_coverage_pct')} net={cand.get('avg_net_edge_bp')} n={cand.get('directional_predictions')} gate={gate}")
            return 0
        return int(mod.main())
    except Exception as exc:
        print(f"[RESEARCH CHAIN FAIL] {label}: {type(exc).__name__}: {exc}")
        return 1


def _run_chain(dataset_root: Path, dates: List[str]) -> int:
    for module_name, filename, marker, url, label in CHAIN:
        print(f"[RESEARCH CHAIN] Continuing into {label} audit.")
        rc = _bootstrap_module(module_name, filename, marker, url, label, dataset_root, dates)
        if rc != 0:
            return rc
    return 0


def main() -> int:
    root = Path.home() / "AStockData" / "qmt_l1_walkforward_v3"
    found = _find_latest_complete_v3(root)
    if found is None:
        print("[IMACD FAIL] no COMPLETE V3 walk-forward run found")
        return 2
    run, v3, dataset_root = found
    dates: List[str] = list(((v3.get("dataset") or {}).get("trade_dates") or []))
    baseline = (((v3.get("aggregates") or {}).get("V4R") or {}).get("logistic_balanced") or {})
    generated_at = datetime.now(base.CN_TZ).isoformat(timespec="seconds")
    print(f"[IMACD] source_run={run.name} dates={len(dates)}")
    print("[IMACD] incomplete/newer V3 folders are ignored automatically")
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

    if os.environ.get("ASTOCK_SKIP_RESEARCH_CHAIN", "0") != "1":
        return _run_chain(dataset_root, dates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
