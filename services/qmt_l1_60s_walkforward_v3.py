# -*- coding: utf-8 -*-
"""QMT L1 60s historical walk-forward audit V3.

Purpose: compare the frozen V4R Champion, V5R conservative control, and V6
execution-aligned challenger on the same QMT tick replay, the same test folds,
and the same 2bp execution hurdle.

This is development backtesting, not pristine OOS for V6: V6 architecture was
informed by earlier Sep-02..Sep-04 observations.  Test folds are never used to
select thresholds or tune parameters inside this audit.  Nothing can promote or
deploy a model; future prospective sessions remain the final judge.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd

import services.qmt_l1_60s_walkforward_v1 as base
import services.qmt_l1_60s_walkforward_v2 as audit

VERSION = "qmt-l1-60s-walkforward-v3-v6-comparison-20260906"
OUT_ROOT = Path.home() / "AStockData" / "qmt_l1_walkforward_v3"
CLOUD_SCOPE = "QMT_L1_60S_WALKFORWARD_V3"
HURDLE_BP = base.HURDLE_BP


def _thresholds_ok(report: Dict[str, Any], variant: str) -> Tuple[bool, List[str]]:
    if variant in {"V4R", "V5R"}:
        return audit._thresholds_ok(report, variant)
    problems: List[str] = []
    import services.train_l1_60s_model_v6_exec_aligned as v6
    version = str(report.get("trainer_version") or "")
    if version != v6.TRAINER_VERSION:
        problems.append(f"trainer_version_mismatch:{version}")
    for family, item in (report.get("models") or {}).items():
        th = item.get("selected_probability_threshold") or {}
        for head in ("up_entry", "down_risk"):
            try:
                value = float(th.get(head))
            except Exception:
                problems.append(f"{family}:{head}:missing_threshold")
                continue
            if not (0.55 - 1e-9 <= value <= 0.90 + 1e-9 or abs(value - 0.999) < 1e-9):
                problems.append(f"{family}:{head}:v6_threshold_out_of_range:{value}")
    return not problems, problems


def _exact_execution_v6(item: Dict[str, Any], frame: pd.DataFrame) -> Dict[str, Any]:
    import services.train_l1_60s_model_v4 as core
    import services.train_l1_60s_model_v5_challenger as v5
    import services.train_l1_60s_model_v6_exec_aligned as v6
    import services.train_l2_60s_model_v3 as splitbase

    model_path = Path(str(item.get("model_path") or ""))
    if not model_path.exists() or frame.empty:
        return {"n": 0}
    bundle = joblib.load(model_path)
    _, _, test, _ = v5._robust_split(frame)
    test = splitbase._nonoverlap(test)
    if test.empty or "ret_ask_to_bid_60_pct" not in test.columns:
        return {"n": 0}
    for c in v6.FEATURES:
        if c not in test.columns:
            test[c] = np.nan
    X = test[v6.FEATURES]
    up_p = core._positive_probability(bundle["up_model"], X)
    dn_p = core._positive_probability(bundle["down_model"], X)
    pred = core._combine(up_p, dn_p, float(bundle["up_threshold"]), float(bundle["down_threshold"]))
    exact = pd.to_numeric(test["ret_ask_to_bid_60_pct"], errors="coerce")
    mask = (pred == 1) & exact.notna().to_numpy()
    vals = exact.loc[mask].dropna().to_numpy(float)
    return {
        "n": int(len(vals)),
        "avg_ask_to_bid_60_edge_bp": float(vals.mean() * 100.0) if len(vals) else None,
        "median_ask_to_bid_60_edge_bp": float(np.median(vals) * 100.0) if len(vals) else None,
        "positive_ask_to_bid_60_pct": float((vals > 0).mean() * 100.0) if len(vals) else None,
        "note": "observed historical ask-now -> future bid diagnostic; signal-quality only under A-share T+1",
    }


def _run_fold(dataset_root: Path, audit_root: Path, dates: List[str], fold_no: int) -> Dict[str, Any]:
    import services.train_l1_60s_model_v4 as core
    import services.train_l1_60s_model_v4r as v4r
    import services.train_l1_60s_model_v5_challenger as v5
    import services.train_l1_60s_model_v5r as v5r
    import services.train_l1_60s_model_v6_exec_aligned as v6

    test_day = dates[-1]
    fold_root = audit_root / f"fold_{fold_no:02d}_{test_day}"
    data_root = fold_root / "data"
    models_v4 = fold_root / "models_v4"
    models_v5 = fold_root / "models_v5"
    models_v6 = fold_root / "models_v6"
    base._copy_fold(dataset_root, data_root, dates)

    core.MODEL_DIR = models_v4
    v4r.MODEL_DIR = models_v4
    v5.MODEL_DIR = models_v5
    v5r.MODEL_DIR = models_v5
    v6.MODEL_DIR = models_v6

    print(f"[FOLD {fold_no}] history through {dates[-2]} -> frozen TEST {test_day}")
    rc4 = audit._capture("V4R", v4r.train, "ALL", 600, data_root, HURDLE_BP)
    rc5 = audit._capture("V5R", v5r.train, "ALL", 600, data_root, HURDLE_BP)
    rc6 = audit._capture("V6", v6.train, "ALL", 600, data_root, HURDLE_BP)

    r4 = base._load_json(models_v4 / "ALL_training_report_latest.json")
    r5 = base._load_json(models_v5 / "ALL_training_report_latest.json")
    r6 = base._load_json(models_v6 / "ALL_training_report_latest.json")

    checks = {}
    for variant, report, rc in (("V4R", r4, rc4), ("V5R", r5, rc5), ("V6", r6, rc6)):
        ok, problems = _thresholds_ok(report, variant)
        checks[variant.lower()] = {"ok": ok, "problems": problems}
        if rc == 0 and not ok:
            raise RuntimeError(f"{variant} audit invariant failed: " + ";".join(problems))

    prepared = core._prepare("ALL", data_root, HURDLE_BP)
    for item in (r4.get("models") or {}).values():
        item["exact_up_entry_execution"] = base._exact_execution(item, prepared, "V4R")
    for item in (r5.get("models") or {}).values():
        item["exact_up_entry_execution"] = base._exact_execution(item, prepared, "V5R")

    prepared_v6 = v6._prepare_exec_aligned("ALL", data_root, HURDLE_BP)
    for item in (r6.get("models") or {}).values():
        item["exact_up_entry_execution"] = _exact_execution_v6(item, prepared_v6)

    return {
        "fold": fold_no,
        "history_dates": dates,
        "test_day": test_day,
        "v4r_rc": rc4,
        "v5r_rc": rc5,
        "v6_rc": rc6,
        "audit_invariants": checks,
        "v4r": r4,
        "v5r": r5,
        "v6": r6,
    }


def _candidate_gate(m: Dict[str, Any]) -> Dict[str, Any]:
    n = int(m.get("directional_predictions") or 0)
    acc = m.get("directional_accuracy_pct")
    coverage = m.get("directional_coverage_pct")
    net = m.get("avg_net_edge_bp")
    pos_days = int(m.get("positive_net_edge_days") or 0)
    wilson = m.get("accuracy_wilson_95_lower_pct")
    checks = {
        "directional_predictions_ge_100": n >= 100,
        "accuracy_ge_55pct": acc is not None and float(acc) >= 55.0,
        "coverage_ge_5pct": coverage is not None and float(coverage) >= 5.0,
        "net_edge_positive": net is not None and float(net) > 0.0,
        "positive_net_edge_days_ge_3": pos_days >= 3,
        "wilson_lower_ge_52pct": wilson is not None and float(wilson) >= 52.0,
    }
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "note": "development research gate only; cannot promote/deploy without future prospective OOS",
    }


def _sync_cloud(report: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from modules.cloud_bridge import CloudBridge, load_bridge_config
        cfg = load_bridge_config()
        bridge = CloudBridge(cfg, timeout=20.0)
        agg = report.get("aggregates") or {}
        test_rows = max(
            int((((agg.get(v) or {}).get("logistic_balanced") or {}).get("test_rows") or 0)
            for v in ("V4R", "V5R", "V6")
        )
        payload = {
            "bridge_id": cfg.bridge_id,
            "scope": CLOUD_SCOPE,
            "trainer_version": VERSION,
            "generated_at": report["generated_at"],
            "maturity": "DEVELOPMENT_BACKTEST_NOT_PRISTINE_OOS",
            "protocol": "QMT_TICK_5S_REPLAY__SAME_FOLDS__V4R_V5R_V6__2BP",
            "samples_total": int((report.get("dataset") or {}).get("rows") or 0),
            "samples_test_nonoverlap": test_rows,
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


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=14, help="calendar lookback; normally contains about 10 trading sessions")
    p.add_argument("--stocks", default=",".join(base.DEFAULT_STOCKS))
    p.add_argument("--end", default="")
    a = p.parse_args()

    now = datetime.now(base.CN_TZ)
    minute = now.hour * 60 + now.minute
    if now.weekday() < 5 and ((570 <= minute < 690) or (780 <= minute < 900)):
        print("[STOP] Historical audit is blocked during market hours to protect QMT live capture.")
        return 3

    stocks = [x.strip().upper() for x in str(a.stocks).split(",") if x.strip()]
    end_d = date.today() if not a.end else datetime.strptime(a.end, "%Y%m%d").date()
    start_d = end_d - timedelta(days=max(14, int(a.days)))
    start, end = start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d")
    stamp = now.strftime("%Y%m%d_%H%M%S")
    run_root = OUT_ROOT / stamp
    dataset_root = run_root / "dataset"
    audit_root = run_root / "audit"
    run_root.mkdir(parents=True, exist_ok=True)

    print(f"AStock QMT historical comparison {VERSION}")
    print("V4R vs V5R vs V6; SAME DATA/FOLDS/HURDLE. DEVELOPMENT ONLY. NO TRADING.")
    try:
        from xtquant import xtdata
    except Exception as exc:
        print(f"[FAIL] xtquant import: {exc}")
        return 2

    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": datetime.now(base.CN_TZ).isoformat(timespec="seconds"),
        "start": start,
        "end": end,
        "stocks": stocks,
        "benchmarks": base.BENCHMARKS,
        "orders_placed": False,
        "live_runtime_stopped": False,
        "qualification": "DEVELOPMENT_BACKTEST_NOT_PRISTINE_OOS",
        "v6_historical_overfit_warning": "V6 architecture was informed by earlier Sep-02..Sep-04 observations; these folds are for research comparison, not final proof.",
        "same_comparison_protocol": {
            "same_qmt_tick_dataset": True,
            "same_test_folds": True,
            "same_execution_hurdle_bp": HURDLE_BP,
            "test_used_for_threshold_tuning": False,
            "max_frozen_test_folds": 5,
        },
        "eligible_for_champion_promotion": False,
        "eligible_for_live_deployment": False,
    }

    v3_mod = original_expand = None
    try:
        report["dataset"] = base._build_dataset(xtdata, stocks, start, end, dataset_root)
        dates = list(report["dataset"]["trade_dates"])
        report["historical_live_parity"] = audit._historical_live_parity(dataset_root, dates)

        start_idx = 5 if len(dates) >= 6 else len(dates) - 1
        test_indices = list(range(start_idx, len(dates)))
        if len(test_indices) > 5:
            test_indices = test_indices[-5:]

        v3_mod, original_expand = base._session_safe_expand_patch()
        folds: List[Dict[str, Any]] = []
        for i, idx in enumerate(test_indices, 1):
            folds.append(_run_fold(dataset_root, audit_root, dates[:idx + 1], i))
        report["folds"] = folds

        report["aggregates"] = {"V4R": {}, "V5R": {}, "V6": {}}
        for variant in ("V4R", "V5R", "V6"):
            for family in ("logistic_balanced", "hist_gradient_boosting"):
                report["aggregates"][variant][family] = base._aggregate(folds, variant, family)

        report["null_control"] = audit._effective_null_control(dataset_root, dates)
        leaderboard = {}
        for variant in ("V4R", "V5R", "V6"):
            m = report["aggregates"][variant]["logistic_balanced"]
            leaderboard[variant] = {
                "directional_accuracy_pct": m.get("directional_accuracy_pct"),
                "directional_predictions": m.get("directional_predictions"),
                "directional_coverage_pct": m.get("directional_coverage_pct"),
                "avg_net_edge_bp": m.get("avg_net_edge_bp"),
                "positive_net_edge_days": m.get("positive_net_edge_days"),
                "wilson_95_lower_pct": m.get("accuracy_wilson_95_lower_pct"),
                "avg_exact_ask_to_bid_60_edge_bp": m.get("avg_exact_ask_to_bid_60_edge_bp"),
                "development_gate": _candidate_gate(m),
            }
        report["leaderboard"] = leaderboard
        report["winner_rule"] = "No upgrade unless Accuracy and Net Edge improve with non-trivial coverage; development gate requires >=55% accuracy, >0bp net, >=5% coverage, >=100 directional predictions, >=3 positive days, Wilson lower >=52%."
    except Exception as exc:
        report["fatal_error"] = f"{type(exc).__name__}: {exc}"
        print(f"[FAIL] {report['fatal_error']}")
    finally:
        if v3_mod is not None and original_expand is not None:
            v3_mod._expand = original_expand

    report["cloud_sync"] = _sync_cloud(report)
    path = run_root / "walkforward_report_v3.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = OUT_ROOT / "latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[REPORT] {path}")

    if report.get("fatal_error"):
        return 1
    print("[RESULT] same-fold development leaderboard")
    for variant in ("V4R", "V5R", "V6"):
        row = (report.get("leaderboard") or {}).get(variant) or {}
        print(
            f"  {variant} accuracy={row.get('directional_accuracy_pct')}% "
            f"n={row.get('directional_predictions')} coverage={row.get('directional_coverage_pct')}% "
            f"net={row.get('avg_net_edge_bp')}bp exact_up={row.get('avg_exact_ask_to_bid_60_edge_bp')}bp "
            f"gate={((row.get('development_gate') or {}).get('pass'))}"
        )
    parity = report.get("historical_live_parity") or {}
    null = report.get("null_control") or {}
    print(f"  parity={parity.get('status')} matched={parity.get('matched_rows')}")
    print(f"  shuffled-null median AUC={null.get('shuffled_auc_median')} leakage_alarm={null.get('leakage_alarm')}")
    print("[IMPORTANT] This comparison can eliminate bad ideas; it cannot certify V6 as pristine OOS. Future unseen sessions remain mandatory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
