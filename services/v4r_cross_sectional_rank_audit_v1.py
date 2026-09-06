# -*- coding: utf-8 -*-
"""Research-only cross-sectional ranking audit for the A-stock 60s project.

The V4R binary heads are kept as frozen score generators, but this audit does
NOT require their absolute probability thresholds to fire.  At each synchronized
60-second bucket across the fixed stock universe, candidates are ranked by the
relative UP-vs-DOWN probability spread.  A small policy family is predeclared;
validation chooses one policy and frozen TEST is evaluated once.

No production model is changed here. Historical success can enter research/
shadow only and still requires prospective unseen sessions before V18 changes.
"""
from __future__ import annotations

import json
import math
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

VERSION = "v4r-cross-sectional-rank-audit-v1-20260906"
CLOUD_SCOPE = "QMT_L1_60S_V4R_XRANK_V1"
HURDLE_BP = 2.0
OUT_ROOT = Path.home() / "AStockData" / "v4r_xrank_v1"

# Fixed before TEST.  Ranking is relative within each 60s cross-section.
POLICIES = ("EXTREMES_1", "UP_1", "DOWN_1", "EXTREMES_2")
MIN_SYMBOLS_PER_BUCKET = 6
MIN_VAL_COVERAGE_PCT = 5.0
MIN_VAL_SIGNALS = 50


def _clip_prob(x: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(x, dtype=float), 1e-6, 1.0 - 1e-6)


def _scores(bundle: Dict[str, Any], frame: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    up = core._positive_probability(bundle["up_model"], frame[core.FEATURES])
    dn = core._positive_probability(bundle["down_model"], frame[core.FEATURES])
    # Log-odds difference preserves ranking and avoids dependence on absolute cutoffs.
    u = _clip_prob(up); d = _clip_prob(dn)
    signed = np.log(u / (1.0 - u)) - np.log(d / (1.0 - d))
    return up, dn, signed


def _rank_pred(frame: pd.DataFrame, score: np.ndarray, policy: str) -> np.ndarray:
    out = np.zeros(len(frame), dtype=int)
    work = frame[["symbol", "generated_ts"]].copy()
    work["_score"] = np.asarray(score, dtype=float)
    work["_row"] = np.arange(len(work), dtype=int)
    work["_bucket"] = (pd.to_numeric(work["generated_ts"], errors="coerce") // 60).astype("Int64")
    for _, g in work.dropna(subset=["_bucket", "_score"]).groupby("_bucket", sort=False):
        # Require a broad cross-section so one missing stock cannot dominate ranking.
        g = g.drop_duplicates("symbol", keep="last")
        if len(g) < MIN_SYMBOLS_PER_BUCKET:
            continue
        g = g.sort_values(["_score", "symbol"], kind="mergesort")
        lo = g.iloc[0:1]; hi = g.iloc[-1:]
        if policy in {"EXTREMES_1", "DOWN_1"}:
            out[lo["_row"].to_numpy(int)] = -1
        if policy in {"EXTREMES_1", "UP_1"}:
            out[hi["_row"].to_numpy(int)] = 1
        if policy == "EXTREMES_2":
            out[g.iloc[:2]["_row"].to_numpy(int)] = -1
            out[g.iloc[-2:]["_row"].to_numpy(int)] = 1
    return out


def _metrics(frame: pd.DataFrame, pred: np.ndarray) -> Dict[str, Any]:
    ret = pd.to_numeric(frame[core.RET_TARGET], errors="coerce").to_numpy(float)
    y = pd.to_numeric(frame[core.ACTION_TARGET], errors="coerce").fillna(0).astype(int).to_numpy()
    use = np.isin(pred, [-1, 1]) & np.isfinite(ret)
    n = int(use.sum()); total = int(len(frame))
    if n:
        acc = float((pred[use] == y[use]).mean() * 100.0)
        signed = np.where(pred[use] == 1, ret[use], -ret[use]) * 100.0
        gross = float(np.mean(signed)); net = gross - HURDLE_BP
    else:
        acc = gross = net = None

    askret = pd.to_numeric(frame.get("ret_ask_to_bid_60_pct"), errors="coerce").to_numpy(float) * 100.0
    bid1 = pd.to_numeric(frame.get("bid1"), errors="coerce").to_numpy(float)
    fbid = pd.to_numeric(frame.get("future_bid_60"), errors="coerce").to_numpy(float)
    downmove = np.where((bid1 > 0) & (fbid > 0), (fbid / bid1 - 1.0) * 10000.0, np.nan)
    exact = np.full(total, np.nan, dtype=float)
    exact[pred == 1] = askret[pred == 1]
    exact[pred == -1] = -downmove[pred == -1]
    ex = exact[use & np.isfinite(exact)]
    return {
        "test_rows": total,
        "directional_predictions": n,
        "directional_coverage_pct": 100.0 * n / total if total else None,
        "directional_accuracy_pct": acc,
        "avg_gross_edge_bp": gross,
        "avg_net_edge_bp": net,
        "exact_exec_n": int(len(ex)),
        "avg_exact_exec_net_edge_bp": float(ex.mean() - HURDLE_BP) if len(ex) else None,
        "exact_exec_win_pct": float((ex > HURDLE_BP).mean() * 100.0) if len(ex) else None,
        "up_predictions": int(((pred == 1) & use).sum()),
        "down_predictions": int(((pred == -1) & use).sum()),
    }


def _select_policy(val: pd.DataFrame, score: np.ndarray) -> Tuple[str, Dict[str, Any]]:
    diagnostics: Dict[str, Any] = {}
    eligible: List[Tuple[str, Dict[str, Any]]] = []
    for name in POLICIES:
        pred = _rank_pred(val, score, name)
        m = _metrics(val, pred)
        diagnostics[name] = m
        if int(m.get("directional_predictions") or 0) >= MIN_VAL_SIGNALS and float(m.get("directional_coverage_pct") or 0.0) >= MIN_VAL_COVERAGE_PCT:
            eligible.append((name, m))
    if not eligible:
        return "EXTREMES_1", diagnostics
    # Economic validation first, then accuracy/sample count. TEST never participates.
    def key(item: Tuple[str, Dict[str, Any]]) -> Tuple[float, float, int]:
        _, m = item
        edge = float(m.get("avg_net_edge_bp") if m.get("avg_net_edge_bp") is not None else -1e9)
        n = int(m.get("directional_predictions") or 0)
        acc = float(m.get("directional_accuracy_pct") or 0.0)
        return edge * math.sqrt(max(1, n)), acc, n
    return max(eligible, key=key)[0], diagnostics


def _wilson_lower(acc_pct: Any, n: int) -> Any:
    if acc_pct is None or n <= 0: return None
    p = float(acc_pct) / 100.0; z = 1.959963984540054
    den = 1.0 + z*z/n; ctr = p + z*z/(2*n)
    rad = z * math.sqrt((p*(1-p) + z*z/(4*n))/n)
    return 100.0 * (ctr-rad) / den


def _aggregate(folds: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    rows = [f[key] for f in folds]
    n = sum(int(x.get("directional_predictions") or 0) for x in rows)
    total = sum(int(x.get("test_rows") or 0) for x in rows)
    correct = sum(float(x.get("directional_accuracy_pct") or 0.0)/100.0 * int(x.get("directional_predictions") or 0) for x in rows)
    gross_num = sum(float(x.get("avg_gross_edge_bp") or 0.0) * int(x.get("directional_predictions") or 0) for x in rows)
    acc = 100.0 * correct / n if n else None
    gross = gross_num / n if n else None
    return {
        "folds": len(rows), "test_rows": total, "directional_predictions": n,
        "directional_coverage_pct": 100.0*n/total if total else None,
        "directional_accuracy_pct": acc,
        "accuracy_wilson_95_lower_pct": _wilson_lower(acc, n),
        "avg_gross_edge_bp": gross,
        "avg_net_edge_bp": gross-HURDLE_BP if gross is not None else None,
        "positive_net_edge_days": sum(1 for x in rows if x.get("avg_net_edge_bp") is not None and float(x["avg_net_edge_bp"]) > 0),
        "daily": [dict(day=f["test_day"], selected_policy=f.get("selected_policy"), **f[key]) for f in folds],
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
    return {"pass": all(checks.values()), "checks": checks}


def _sync(report: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from modules.cloud_bridge import CloudBridge, load_bridge_config
        cfg = load_bridge_config(); bridge = CloudBridge(cfg, timeout=20.0)
        payload = {
            "bridge_id": cfg.bridge_id, "scope": CLOUD_SCOPE, "trainer_version": VERSION,
            "generated_at": report["generated_at"], "maturity": "DEVELOPMENT_BACKTEST_NOT_PRISTINE_OOS",
            "protocol": "V4R_HEAD_SCORES__CROSS_SECTIONAL_60S_RANK__VALIDATION_POLICY__FROZEN_TEST__2BP",
            "samples_total": int(report.get("samples_total") or 0),
            "samples_test_nonoverlap": int((report.get("candidate") or {}).get("test_rows") or 0),
            "report": report,
        }
        bridge._request("POST", "ml_training_reports_v1?on_conflict=bridge_id,scope,generated_at", json=payload,
                        headers={"Prefer":"resolution=merge-duplicates,return=minimal"})
        return {"ok": True, "scope": CLOUD_SCOPE}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def run(dataset_root: Path, dates: List[str]) -> Dict[str, Any]:
    dates = list(dates)
    start_idx = 5 if len(dates) >= 6 else len(dates)-1
    test_indices = list(range(start_idx, len(dates)))[-5:]
    folds: List[Dict[str, Any]] = []
    v3_mod, original_expand = base._session_safe_expand_patch()
    try:
        for fold_no, idx in enumerate(test_indices, 1):
            history = dates[:idx+1]; test_day = history[-1]
            fold_root = OUT_ROOT / f"fold_{fold_no:02d}_{test_day}"
            data_root = fold_root / "data"; model_root = fold_root / "models"
            if fold_root.exists(): shutil.rmtree(fold_root)
            base._copy_fold(dataset_root, data_root, history)
            core.MODEL_DIR = model_root; v4r.MODEL_DIR = model_root
            rc = v4r.train("ALL", 600, data_root, HURDLE_BP)
            if rc != 0: raise RuntimeError(f"V4R train failed fold {fold_no} rc={rc}")
            train_report = base._load_json(model_root / "ALL_training_report_latest.json")
            item = (train_report.get("models") or {}).get("logistic_balanced") or {}
            bundle = joblib.load(Path(str(item.get("model_path") or "")))
            full = core._prepare("ALL", data_root, HURDLE_BP)
            _, val, test, _ = splitbase._split(full)
            val = splitbase._nonoverlap(val); test = splitbase._nonoverlap(test)
            _, _, vscore = _scores(bundle, val); _, _, tscore = _scores(bundle, test)
            policy, diagnostics = _select_policy(val, vscore)
            v4pred = core._combine(
                core._positive_probability(bundle["up_model"], test[core.FEATURES]),
                core._positive_probability(bundle["down_model"], test[core.FEATURES]),
                float(bundle["up_threshold"]), float(bundle["down_threshold"]),
            )
            rankpred = _rank_pred(test, tscore, policy)
            folds.append({
                "fold": fold_no, "test_day": test_day, "selected_policy": policy,
                "validation_policy_diagnostics": diagnostics,
                "baseline": _metrics(test, v4pred), "candidate": _metrics(test, rankpred),
                "test_used_for_policy_selection": False,
            })
    finally:
        v3_mod._expand = original_expand
    baseline = _aggregate(folds, "baseline"); candidate = _aggregate(folds, "candidate")
    report = {
        "version": VERSION, "generated_at": datetime.now(base.CN_TZ).isoformat(timespec="seconds"),
        "qualification": "DEVELOPMENT_BACKTEST_NOT_PRISTINE_OOS",
        "policies_predeclared": list(POLICIES), "minimum_symbols_per_bucket": MIN_SYMBOLS_PER_BUCKET,
        "ranking_score": "logit(up_probability)-logit(down_probability)",
        "absolute_probability_threshold_required": False,
        "validation_only_policy_selection": True, "test_used_for_selection": False,
        "same_2bp_hurdle": True, "same_60s_nonoverlap_test": True,
        "folds": folds, "baseline": baseline, "candidate": candidate,
        "development_gate": _gate(candidate, baseline),
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
    v3r = json.loads(source.read_text(encoding="utf-8")); dates = list((v3r.get("dataset") or {}).get("trade_dates") or [])
    candidates = sorted(root.glob("*/dataset"), key=lambda p:p.stat().st_mtime, reverse=True)
    if not candidates or len(dates) < 6:
        print("[STOP] Reusable V3 dataset not found or too short.")
        return 3
    report = run(candidates[0], dates)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    b, c = report["baseline"], report["candidate"]
    print(f"[XRANK BASE] acc={b.get('directional_accuracy_pct')} cov={b.get('directional_coverage_pct')} net={b.get('avg_net_edge_bp')} n={b.get('directional_predictions')}")
    print(f"[XRANK] acc={c.get('directional_accuracy_pct')} cov={c.get('directional_coverage_pct')} net={c.get('avg_net_edge_bp')} n={c.get('directional_predictions')} gate={report['development_gate']['pass']}")
    print("[IMPORTANT] Historical pass may enter research/shadow only; production remains untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
