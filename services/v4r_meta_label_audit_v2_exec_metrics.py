# -*- coding: utf-8 -*-
"""Research-only V4R meta-label audit V2 with exact-execution primary metrics.

V1 already trains the meta-label on exact signed execution edge, but its primary
Accuracy / Gross Edge / Net Edge gate is still inherited from the V4R smoothed-mid
return metric.  V2 changes ONLY the evaluation/scoring semantics so training target
and promotion metrics are economically aligned:

- UP exact edge: observed ask-now -> observed future bid (+60s proxy already carried
  by V1's meta_exact_signed_edge_bp field).
- DOWN exact edge: avoided loss from observed bid-now -> observed future bid.
- Accuracy: exact signed execution edge > 2bp.
- Gross Edge: mean exact signed execution edge (bp).
- Net Edge: Gross Edge - 2bp.

The V4R direction model, meta feature set, meta Logistic C=0.10, validation 60/40
fit/select split, threshold family, chronological outer folds and test isolation are
unchanged.  This module is DEVELOPMENT RESEARCH ONLY and cannot auto-deploy.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

import services.v4r_meta_label_audit_v1 as v1

VERSION = "v4r-meta-label-exact-exec-metrics-v2-20260908"
CLOUD_SCOPE = "QMT_L1_60S_V4R_META_LABEL_V2_EXEC"
HURDLE_BP = v1.HURDLE_BP
OUT_ROOT = Path.home() / "AStockData" / "v4r_meta_label_v2_exec_metrics"


def _metrics(frame: pd.DataFrame, pred: np.ndarray, keep: np.ndarray) -> Dict[str, Any]:
    pred = np.asarray(pred, dtype=int)
    keep = np.asarray(keep, dtype=bool)
    exact = pd.to_numeric(frame.get("meta_exact_signed_edge_bp"), errors="coerce").to_numpy(float)
    use = keep & np.isin(pred, [-1, 1]) & np.isfinite(exact)
    n = int(use.sum())
    total = int(len(frame))
    if n:
        ex = exact[use]
        acc = float((ex > HURDLE_BP).mean() * 100.0)
        gross = float(ex.mean())
        net = gross - HURDLE_BP
    else:
        ex = np.asarray([], dtype=float)
        acc = gross = net = None
    return {
        "test_rows": total,
        "directional_predictions": n,
        "directional_coverage_pct": 100.0 * n / total if total else None,
        "directional_accuracy_pct": acc,
        "avg_gross_edge_bp": gross,
        "avg_net_edge_bp": net,
        "exact_exec_n": n,
        "avg_exact_exec_net_edge_bp": net,
        "exact_exec_win_pct": acc,
        "up_predictions": int(((pred == 1) & use).sum()),
        "down_predictions": int(((pred == -1) & use).sum()),
        "primary_metric_policy": "EXACT_SIGNED_EXECUTION_EDGE_GT_2BP",
    }


def _aggregate(folds: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    rows = [f[key] for f in folds]
    n = sum(int(x.get("directional_predictions") or 0) for x in rows)
    total = sum(int(x.get("test_rows") or 0) for x in rows)
    correct = sum(
        float(x.get("directional_accuracy_pct") or 0.0) / 100.0
        * int(x.get("directional_predictions") or 0)
        for x in rows
    )
    gross_num = sum(
        float(x.get("avg_gross_edge_bp") or 0.0)
        * int(x.get("directional_predictions") or 0)
        for x in rows
    )
    acc = 100.0 * correct / n if n else None
    gross = gross_num / n if n else None
    return {
        "folds": len(rows),
        "test_rows": total,
        "directional_predictions": n,
        "directional_coverage_pct": 100.0 * n / total if total else None,
        "directional_accuracy_pct": acc,
        "accuracy_wilson_95_lower_pct": v1._wilson_lower(acc, n),
        "avg_gross_edge_bp": gross,
        "avg_net_edge_bp": gross - HURDLE_BP if gross is not None else None,
        "avg_exact_exec_net_edge_bp": gross - HURDLE_BP if gross is not None else None,
        "positive_net_edge_days": sum(
            1 for x in rows
            if x.get("avg_net_edge_bp") is not None and float(x["avg_net_edge_bp"]) > 0.0
        ),
        "daily": [dict(day=f["test_day"], meta_threshold=f.get("meta_threshold"), **f[key]) for f in folds],
        "primary_metric_policy": "EXACT_SIGNED_EXECUTION_EDGE_GT_2BP",
    }


def _gate(candidate: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    checks = {
        "directional_predictions_ge_100": int(candidate.get("directional_predictions") or 0) >= 100,
        "accuracy_ge_55pct": candidate.get("directional_accuracy_pct") is not None and float(candidate["directional_accuracy_pct"]) >= 55.0,
        "coverage_ge_5pct": candidate.get("directional_coverage_pct") is not None and float(candidate["directional_coverage_pct"]) >= 5.0,
        "net_edge_positive": candidate.get("avg_net_edge_bp") is not None and float(candidate["avg_net_edge_bp"]) > 0.0,
        "positive_net_edge_days_ge_3": int(candidate.get("positive_net_edge_days") or 0) >= 3,
        "wilson_lower_ge_52pct": candidate.get("accuracy_wilson_95_lower_pct") is not None and float(candidate["accuracy_wilson_95_lower_pct"]) >= 52.0,
        "beats_v4r_accuracy": candidate.get("directional_accuracy_pct") is not None and baseline.get("directional_accuracy_pct") is not None and float(candidate["directional_accuracy_pct"]) > float(baseline["directional_accuracy_pct"]),
        "beats_v4r_net_edge": candidate.get("avg_net_edge_bp") is not None and baseline.get("avg_net_edge_bp") is not None and float(candidate["avg_net_edge_bp"]) > float(baseline["avg_net_edge_bp"]),
    }
    return {"pass": all(checks.values()), "checks": checks, "metric_policy": "EXACT_EXECUTION_PRIMARY"}


def _sync(report: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from modules.cloud_bridge import CloudBridge, load_bridge_config
        cfg = load_bridge_config()
        bridge = CloudBridge(cfg, timeout=20.0)
        payload = {
            "bridge_id": cfg.bridge_id,
            "scope": CLOUD_SCOPE,
            "trainer_version": VERSION,
            "generated_at": report["generated_at"],
            "maturity": "DEVELOPMENT_BACKTEST_NOT_PRISTINE_OOS",
            "protocol": "V4R_DIRECTION__VAL60_META_FIT40_SELECT__EXACT_EXEC_TARGET_AND_PRIMARY_METRICS__2BP",
            "samples_total": int(report.get("samples_total") or 0),
            "samples_test_nonoverlap": int((report.get("candidate") or {}).get("test_rows") or 0),
            "report": report,
        }
        bridge._request(
            "POST",
            "ml_training_reports_v1?on_conflict=bridge_id,scope,generated_at",
            json=payload,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        return {"ok": True, "scope": CLOUD_SCOPE}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def run(dataset_root: Path, dates: List[str]) -> Dict[str, Any]:
    old = {
        "metrics": v1._metrics,
        "aggregate": v1._aggregate,
        "gate": v1._gate,
        "sync": v1._sync,
        "out_root": v1.OUT_ROOT,
        "version": v1.VERSION,
        "scope": v1.CLOUD_SCOPE,
    }
    try:
        v1._metrics = _metrics
        v1._aggregate = _aggregate
        v1._gate = _gate
        v1._sync = _sync
        v1.OUT_ROOT = OUT_ROOT
        v1.VERSION = VERSION
        v1.CLOUD_SCOPE = CLOUD_SCOPE
        report = v1.run(dataset_root, dates)
        report["version"] = VERSION
        report["qualification"] = "DEVELOPMENT_BACKTEST_NOT_PRISTINE_OOS"
        report["primary_metric_policy"] = "EXACT_SIGNED_EXECUTION_EDGE_GT_2BP"
        report["v1_difference"] = "evaluation/gate only; direction model, meta model, features, folds, fit/select split and thresholds unchanged"
        report["eligible_for_live_deployment"] = False
        return report
    finally:
        v1._metrics = old["metrics"]
        v1._aggregate = old["aggregate"]
        v1._gate = old["gate"]
        v1._sync = old["sync"]
        v1.OUT_ROOT = old["out_root"]
        v1.VERSION = old["version"]
        v1.CLOUD_SCOPE = old["scope"]


def main() -> int:
    root = Path.home() / "AStockData" / "qmt_l1_walkforward_v3"
    source = root / "latest.json"
    if not source.exists():
        print("[STOP] No local V3 walk-forward report found; run the single research BAT first.")
        return 3
    v3r = json.loads(source.read_text(encoding="utf-8"))
    dates = list((v3r.get("dataset") or {}).get("trade_dates") or [])
    candidates = sorted(root.glob("*/dataset"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates or len(dates) < 6:
        print("[STOP] Reusable V3 dataset not found or too short.")
        return 3
    report = run(candidates[0], dates)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    b, c = report["baseline"], report["candidate"]
    print(f"[META V2 BASE] acc={b.get('directional_accuracy_pct')} cov={b.get('directional_coverage_pct')} net={b.get('avg_net_edge_bp')} n={b.get('directional_predictions')}")
    print(f"[META V2 FILTER] acc={c.get('directional_accuracy_pct')} cov={c.get('directional_coverage_pct')} net={c.get('avg_net_edge_bp')} n={c.get('directional_predictions')} gate={report['development_gate']['pass']}")
    print("[IMPORTANT] Exact-execution metrics are primary. Historical pass cannot promote production.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
