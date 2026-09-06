# -*- coding: utf-8 -*-
"""Leak-resistant rolling OOF meta-label audit V2.

For every day used by the meta layer, V4R is first trained only on earlier days
and predicts that day out-of-sample. Two prior OOF days fit the meta model, the
next OOF day selects a fixed meta threshold, and the final OOF day is frozen
TEST. This avoids training the meta layer on a day that V4R used for threshold
selection. Research only; production V18 is untouched.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd

import services.qmt_l1_60s_walkforward_v1 as base
import services.train_l1_60s_model_v4 as core
import services.train_l1_60s_model_v4r as v4r
import services.train_l2_60s_model_v3 as splitbase
import services.v4r_meta_label_audit_v1 as m1

VERSION = "v4r-meta-label-rolling-oof-v2-20260907"
CLOUD_SCOPE = "QMT_L1_60S_V4R_META_LABEL_V2"
OUT_ROOT = Path.home() / "AStockData" / "v4r_meta_label_v2"
HURDLE_BP = 2.0
META_TRAIN_DAYS = 2


def _sync(report: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from modules.cloud_bridge import CloudBridge, load_bridge_config
        cfg = load_bridge_config(); bridge = CloudBridge(cfg, timeout=20.0)
        payload = {
            "bridge_id": cfg.bridge_id, "scope": CLOUD_SCOPE, "trainer_version": VERSION,
            "generated_at": report["generated_at"], "maturity": "DEVELOPMENT_BACKTEST_NOT_PRISTINE_OOS",
            "protocol": "ROLLING_V4R_OOF__2_OOF_DAYS_META_FIT__NEXT_OOF_DAY_VAL__FROZEN_OOF_TEST__2BP",
            "samples_total": int(report.get("samples_total") or 0),
            "samples_test_nonoverlap": int((report.get("candidate") or {}).get("test_rows") or 0),
            "report": report,
        }
        bridge._request("POST", "ml_training_reports_v1?on_conflict=bridge_id,scope,generated_at", json=payload,
                        headers={"Prefer":"resolution=merge-duplicates,return=minimal"})
        return {"ok": True, "scope": CLOUD_SCOPE}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _predict_oof_day(dataset_root: Path, all_dates: List[str], target_day: str,
                     cache: Dict[str, Tuple[pd.DataFrame, np.ndarray]]) -> Tuple[pd.DataFrame, np.ndarray]:
    if target_day in cache:
        frame, pred = cache[target_day]
        return frame.copy(), pred.copy()
    idx = all_dates.index(target_day)
    history = all_dates[:idx+1]
    if len(history) < 4:
        raise RuntimeError(f"insufficient history for OOF base prediction: {target_day}")
    root = OUT_ROOT / "base_cache" / target_day
    data_root = root / "data"; model_root = root / "models"
    if root.exists(): shutil.rmtree(root)
    base._copy_fold(dataset_root, data_root, history)
    core.MODEL_DIR = model_root; v4r.MODEL_DIR = model_root
    rc = v4r.train("ALL", 600, data_root, HURDLE_BP)
    if rc != 0:
        raise RuntimeError(f"V4R OOF train failed day={target_day} rc={rc}")
    r = base._load_json(model_root / "ALL_training_report_latest.json")
    item = (r.get("models") or {}).get("logistic_balanced") or {}
    bundle = joblib.load(Path(str(item.get("model_path") or "")))
    full = core._prepare("ALL", data_root, HURDLE_BP)
    _, _, test, _ = splitbase._split(full)
    test = splitbase._nonoverlap(test)
    test = m1._enrich_imacd(full, test.copy())
    up, dn, pred = m1._pred_probs(bundle, test)
    testm = m1._meta_frame(test, up, dn, pred, float(bundle["up_threshold"]), float(bundle["down_threshold"]))
    cache[target_day] = (testm.copy(), pred.copy())
    return testm, pred


def run(dataset_root: Path, dates: List[str]) -> Dict[str, Any]:
    dates = list(dates)
    # Need two OOF meta-fit days + one OOF validation day + frozen test, each base OOF day itself needing history.
    eligible_test_indices = [i for i in range(len(dates)) if i >= 6]
    test_indices = eligible_test_indices[-5:]
    folds: List[Dict[str, Any]] = []
    cache: Dict[str, Tuple[pd.DataFrame, np.ndarray]] = {}
    v3_mod, original_expand = base._session_safe_expand_patch()
    try:
        for fold_no, idx in enumerate(test_indices, 1):
            test_day = dates[idx]
            val_day = dates[idx-1]
            meta_days = dates[idx-1-META_TRAIN_DAYS:idx-1]
            if len(meta_days) != META_TRAIN_DAYS:
                continue
            fit_parts: List[pd.DataFrame] = []
            for d in meta_days:
                dm, dp = _predict_oof_day(dataset_root, dates, d, cache)
                active = dm[np.isin(dp, [-1,1]) & pd.to_numeric(dm["meta_exact_signed_edge_bp"], errors="coerce").notna()].copy()
                fit_parts.append(active)
            fit = pd.concat(fit_parts, ignore_index=True) if fit_parts else pd.DataFrame()
            valm, vp = _predict_oof_day(dataset_root, dates, val_day, cache)
            testm, tp = _predict_oof_day(dataset_root, dates, test_day, cache)

            if len(fit) < m1.MIN_META_FIT_SIGNALS or fit["meta_tradeable"].nunique() < 2:
                meta_t = 1.1
                diagnostics: Dict[str, Any] = {"error":"insufficient_or_single_class_oof_meta_fit", "n":int(len(fit))}
                tprob = np.zeros(len(testm), dtype=float)
            else:
                model = m1._model()
                model.fit(fit[m1.META_FEATURES], fit["meta_tradeable"].astype(int))
                vprob = model.predict_proba(valm[m1.META_FEATURES])[:,1]
                meta_t, diagnostics = m1._choose_threshold(valm, vp, vprob)
                tprob = model.predict_proba(testm[m1.META_FEATURES])[:,1]

            baseline_keep = np.isin(tp, [-1,1])
            candidate_keep = baseline_keep & (tprob >= meta_t)
            folds.append({
                "fold": fold_no, "test_day": test_day,
                "meta_fit_oof_days": list(meta_days), "meta_validation_oof_day": val_day,
                "meta_threshold": meta_t, "validation_meta_diagnostics": diagnostics,
                "baseline": m1._metrics(testm, tp, baseline_keep),
                "candidate": m1._metrics(testm, tp, candidate_keep),
                "all_meta_inputs_oof_to_base": True,
                "test_used_for_meta_fit_or_selection": False,
            })
    finally:
        v3_mod._expand = original_expand

    baseline = m1._aggregate(folds, "baseline")
    candidate = m1._aggregate(folds, "candidate")
    report = {
        "version": VERSION, "generated_at": datetime.now(base.CN_TZ).isoformat(timespec="seconds"),
        "qualification": "DEVELOPMENT_BACKTEST_NOT_PRISTINE_OOS",
        "architecture": "V4R direction -> rolling OOF meta tradeability filter",
        "meta_target": "exact_signed_execution_edge_gt_2bp",
        "meta_fit_days": META_TRAIN_DAYS,
        "meta_thresholds_predeclared": list(m1.META_THRESHOLDS),
        "meta_features_predeclared": list(m1.META_FEATURES),
        "all_meta_fit_validation_test_base_predictions_oof": True,
        "test_used_for_selection": False, "same_2bp_hurdle": True, "same_60s_nonoverlap_test": True,
        "folds": folds, "baseline": baseline, "candidate": candidate,
        "development_gate": m1._gate(candidate, baseline),
        "samples_total": int(candidate.get("test_rows") or 0), "eligible_for_live_deployment": False,
    }
    report["cloud_sync"] = _sync(report)
    return report


def main() -> int:
    root = Path.home() / "AStockData" / "qmt_l1_walkforward_v3"
    source = root / "latest.json"
    if not source.exists():
        print("[STOP] No local V3 walk-forward report found; run the single research BAT first.")
        return 3
    v3r = json.loads(source.read_text(encoding="utf-8"))
    dates = list((v3r.get("dataset") or {}).get("trade_dates") or [])
    candidates = sorted(root.glob("*/dataset"), key=lambda p:p.stat().st_mtime, reverse=True)
    if not candidates or len(dates) < 7:
        print("[STOP] Reusable V3 dataset not found or too short for rolling OOF meta audit.")
        return 3
    report = run(candidates[0], dates)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    b, c = report["baseline"], report["candidate"]
    print(f"[META2 BASE] acc={b.get('directional_accuracy_pct')} cov={b.get('directional_coverage_pct')} net={b.get('avg_net_edge_bp')} n={b.get('directional_predictions')}")
    print(f"[META2] acc={c.get('directional_accuracy_pct')} cov={c.get('directional_coverage_pct')} net={c.get('avg_net_edge_bp')} exact_net={c.get('avg_exact_exec_net_edge_bp')} n={c.get('directional_predictions')} gate={report['development_gate']['pass']}")
    print("[IMPORTANT] Historical pass may enter research/shadow only; production remains untouched.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
