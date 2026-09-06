# -*- coding: utf-8 -*-
"""Research-only V4R meta-label audit.

V4R remains the primary direction model.  This audit fits a second-stage model
only on already-active V4R signals and asks: should this signal be traded?

Leakage controls:
- same outer chronological V4R walk-forward folds as the frozen audit;
- outer TEST is never used to fit the meta model or choose its threshold;
- the single outer validation day is split chronologically: first 60% of V4R
  signals fit the meta model, last 40% select one fixed threshold;
- meta target is observed signed execution edge > 2bp using ask-now->future-bid
  for UP and bid-now->future-bid avoidance for DOWN;
- threshold family and feature set are predeclared here before test results;
- test remains 60s non-overlapping and is evaluated once.

Development research only. Production V18 is never modified by this module.
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
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

import services.imacd_research_audit_v1 as imacd
import services.qmt_l1_60s_walkforward_v1 as base
import services.train_l1_60s_model_v4 as core
import services.train_l1_60s_model_v4r as v4r
import services.train_l2_60s_model_v3 as splitbase

VERSION = "v4r-meta-label-exec-audit-v1-20260906"
CLOUD_SCOPE = "QMT_L1_60S_V4R_META_LABEL_V1"
HURDLE_BP = 2.0
OUT_ROOT = Path.home() / "AStockData" / "v4r_meta_label_v1"
META_THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
MIN_META_FIT_SIGNALS = 50
MIN_META_SELECT_SIGNALS = 30
MIN_SELECT_KEPT = 15

META_FEATURES = [
    "meta_base_confidence", "meta_base_margin", "meta_signal_is_up",
    "meta_spread_pct", "meta_change_10s_signed", "meta_change_30s_signed",
    "meta_change_60s_signed", "meta_vwap_signed", "meta_tick_flow_signed",
    "meta_book_flow_signed", "meta_pressure_change_signed",
    "meta_microprice_signed", "meta_depth_imbalance_signed",
    "meta_imacd_aligned", "meta_imacd_opposed", "meta_imacd_own_exhaust",
    "meta_imacd_whipsaw", "meta_opening_core",
]


def _pred_probs(bundle: Dict[str, Any], frame: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    up = core._positive_probability(bundle["up_model"], frame[core.FEATURES])
    dn = core._positive_probability(bundle["down_model"], frame[core.FEATURES])
    pred = core._combine(up, dn, float(bundle["up_threshold"]), float(bundle["down_threshold"]))
    return up, dn, pred


def _enrich_imacd(full: pd.DataFrame, part: pd.DataFrame) -> pd.DataFrame:
    x = full.copy()
    if "trade_date" not in x.columns:
        x["trade_date"] = pd.to_datetime(x["generated_at"], errors="coerce").dt.date.astype(str)
    if "last_price" not in x.columns and "lastPrice" in x.columns:
        x["last_price"] = pd.to_numeric(x["lastPrice"], errors="coerce")
    enriched = imacd._add_imacd(x)
    by_ts = enriched.set_index(["symbol", "generated_ts"], drop=False)
    keys = pd.MultiIndex.from_frame(part[["symbol", "generated_ts"]])
    z = by_ts.reindex(keys).reset_index(drop=True)
    out = part.copy()
    z.index = out.index
    for c in ("imacd_state_up", "imacd_state_down", "imacd_up_exhaust", "imacd_down_exhaust", "imacd_state"):
        if c in z.columns:
            out[c] = z[c].to_numpy()
        else:
            out[c] = "NEUTRAL" if c == "imacd_state" else 0.0
    return out


def _num(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(default)


def _meta_frame(frame: pd.DataFrame, up: np.ndarray, dn: np.ndarray, pred: np.ndarray,
                up_t: float, dn_t: float) -> pd.DataFrame:
    out = frame.copy()
    sig = pred.astype(int)
    signed = sig.astype(float)
    up_margin = (up - up_t) / max(1e-9, 1.0 - up_t)
    dn_margin = (dn - dn_t) / max(1e-9, 1.0 - dn_t)
    chosen_margin = np.where(sig == 1, up_margin, np.where(sig == -1, dn_margin, 0.0))
    other_margin = np.where(sig == 1, dn_margin, np.where(sig == -1, up_margin, 0.0))
    out["meta_base_confidence"] = np.where(sig == 1, up, np.where(sig == -1, dn, 0.0))
    out["meta_base_margin"] = chosen_margin - other_margin
    out["meta_signal_is_up"] = (sig == 1).astype(float)
    out["meta_spread_pct"] = _num(out, "spread_pct")
    out["meta_change_10s_signed"] = _num(out, "change_10s_pct") * signed
    out["meta_change_30s_signed"] = _num(out, "change_30s_pct") * signed
    out["meta_change_60s_signed"] = _num(out, "change_60s_pct") * signed
    out["meta_vwap_signed"] = _num(out, "above_vwap_pct") * signed
    out["meta_tick_flow_signed"] = ((_num(out, "tick_buy_pct", 50.0) - 50.0) / 50.0) * signed
    out["meta_book_flow_signed"] = ((_num(out, "book_buy_pressure_pct", 50.0) - 50.0) / 50.0) * signed
    out["meta_pressure_change_signed"] = _num(out, "pressure_change_pct") * signed
    out["meta_microprice_signed"] = _num(out, "microprice_vs_mid_pct") * signed
    out["meta_depth_imbalance_signed"] = _num(out, "depth5_imbalance_pct") * signed

    st_up = _num(out, "imacd_state_up") > 0.5
    st_dn = _num(out, "imacd_state_down") > 0.5
    up_ex = _num(out, "imacd_up_exhaust") > 0.5
    dn_ex = _num(out, "imacd_down_exhaust") > 0.5
    state = out.get("imacd_state", pd.Series("NEUTRAL", index=out.index)).astype(str)
    out["meta_imacd_aligned"] = (((sig == 1) & st_up) | ((sig == -1) & st_dn)).astype(float)
    out["meta_imacd_opposed"] = (((sig == 1) & st_dn) | ((sig == -1) & st_up)).astype(float)
    out["meta_imacd_own_exhaust"] = (((sig == 1) & up_ex) | ((sig == -1) & dn_ex)).astype(float)
    out["meta_imacd_whipsaw"] = (state == "WHIPSAW").astype(float)
    minute = _num(out, "minute_of_day", -1.0)
    out["meta_opening_core"] = ((minute >= 570) & (minute < 630)).astype(float)

    askret = _num(out, "ret_ask_to_bid_60_pct", np.nan).to_numpy(float) * 100.0
    bid1 = _num(out, "bid1", np.nan).to_numpy(float)
    fbid = _num(out, "future_bid_60", np.nan).to_numpy(float)
    down_move_bp = np.where((bid1 > 0) & (fbid > 0), (fbid / bid1 - 1.0) * 10000.0, np.nan)
    exact_signed_bp = np.full(len(out), np.nan, dtype=float)
    exact_signed_bp[sig == 1] = askret[sig == 1]
    exact_signed_bp[sig == -1] = -down_move_bp[sig == -1]
    out["meta_exact_signed_edge_bp"] = exact_signed_bp
    out["meta_tradeable"] = (exact_signed_bp > HURDLE_BP).astype(int)
    out["meta_pred_direction"] = sig
    return out


def _model() -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scale", RobustScaler(quantile_range=(25.0, 75.0))),
        ("model", LogisticRegression(C=0.10, class_weight="balanced", max_iter=1600, solver="lbfgs")),
    ])


def _metrics(frame: pd.DataFrame, pred: np.ndarray, keep: np.ndarray) -> Dict[str, Any]:
    ret = pd.to_numeric(frame[core.RET_TARGET], errors="coerce").to_numpy(float)
    y = pd.to_numeric(frame[core.ACTION_TARGET], errors="coerce").fillna(0).astype(int).to_numpy()
    use = keep & np.isfinite(ret) & np.isin(pred, [-1, 1])
    n = int(use.sum()); total = int(len(frame))
    if n:
        acc = float((pred[use] == y[use]).mean() * 100.0)
        signed = np.where(pred[use] == 1, ret[use], -ret[use]) * 100.0
        gross = float(np.mean(signed)); net = gross - HURDLE_BP
    else:
        acc = gross = net = None
    exact = pd.to_numeric(frame.get("meta_exact_signed_edge_bp"), errors="coerce").to_numpy(float)
    ex = exact[use & np.isfinite(exact)]
    return {
        "test_rows": total, "directional_predictions": n,
        "directional_coverage_pct": 100.0 * n / total if total else None,
        "directional_accuracy_pct": acc, "avg_gross_edge_bp": gross,
        "avg_net_edge_bp": net,
        "exact_exec_n": int(len(ex)),
        "avg_exact_exec_net_edge_bp": float(ex.mean() - HURDLE_BP) if len(ex) else None,
        "exact_exec_win_pct": float((ex > HURDLE_BP).mean() * 100.0) if len(ex) else None,
        "up_predictions": int(((pred == 1) & use).sum()),
        "down_predictions": int(((pred == -1) & use).sum()),
    }


def _choose_threshold(select: pd.DataFrame, pred: np.ndarray, prob: np.ndarray) -> Tuple[float, Dict[str, Any]]:
    diagnostics: Dict[str, Any] = {}
    best_t = 1.1; best_score = (-1e18, -1e18, -1)
    active = np.isin(pred, [-1, 1])
    for t in META_THRESHOLDS:
        keep = active & (prob >= t)
        m = _metrics(select, pred, keep)
        diagnostics[f"p{int(t*100)}"] = m
        n = int(m.get("directional_predictions") or 0)
        exact = m.get("avg_exact_exec_net_edge_bp")
        acc = float(m.get("directional_accuracy_pct") or 0.0)
        if n < MIN_SELECT_KEPT or exact is None:
            continue
        score = (float(exact) * math.sqrt(n), acc, n)
        if score > best_score:
            best_score = score; best_t = float(t)
    return best_t, diagnostics


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
    ex_n = sum(int(x.get("exact_exec_n") or 0) for x in rows)
    ex_net_num = sum(float(x.get("avg_exact_exec_net_edge_bp") or 0.0) * int(x.get("exact_exec_n") or 0) for x in rows)
    acc = 100.0 * correct / n if n else None
    gross = gross_num / n if n else None
    return {
        "folds": len(rows), "test_rows": total, "directional_predictions": n,
        "directional_coverage_pct": 100.0*n/total if total else None,
        "directional_accuracy_pct": acc,
        "accuracy_wilson_95_lower_pct": _wilson_lower(acc, n),
        "avg_gross_edge_bp": gross,
        "avg_net_edge_bp": gross-HURDLE_BP if gross is not None else None,
        "avg_exact_exec_net_edge_bp": ex_net_num/ex_n if ex_n else None,
        "positive_net_edge_days": sum(1 for x in rows if x.get("avg_net_edge_bp") is not None and float(x["avg_net_edge_bp"]) > 0),
        "daily": [dict(day=f["test_day"], meta_threshold=f.get("meta_threshold"), **f[key]) for f in folds],
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
            "protocol": "V4R_DIRECTION__VAL60_META_FIT40_SELECT__EXACT_EXEC_TARGET__SAME_TEST__2BP",
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
            val = _enrich_imacd(full, val.copy()); test = _enrich_imacd(full, test.copy())
            vu, vd, vp = _pred_probs(bundle, val); tu, td, tp = _pred_probs(bundle, test)
            valm = _meta_frame(val, vu, vd, vp, float(bundle["up_threshold"]), float(bundle["down_threshold"]))
            testm = _meta_frame(test, tu, td, tp, float(bundle["up_threshold"]), float(bundle["down_threshold"]))
            active = valm[np.isin(vp, [-1, 1]) & pd.to_numeric(valm["meta_exact_signed_edge_bp"], errors="coerce").notna()].sort_values("generated_ts")
            if len(active) < MIN_META_FIT_SIGNALS + MIN_META_SELECT_SIGNALS:
                meta_t = 1.1; diagnostics = {"error":"insufficient_validation_signals", "n":int(len(active))}
                tprob = np.zeros(len(testm), dtype=float)
            else:
                cut = int(math.floor(len(active) * 0.60))
                cut = max(MIN_META_FIT_SIGNALS, min(cut, len(active)-MIN_META_SELECT_SIGNALS))
                fit = active.iloc[:cut].copy(); sel = active.iloc[cut:].copy()
                if fit["meta_tradeable"].nunique() < 2:
                    meta_t = 1.1; diagnostics = {"error":"single_class_meta_fit", "n":int(len(fit))}
                    tprob = np.zeros(len(testm), dtype=float)
                else:
                    model = _model(); model.fit(fit[META_FEATURES], fit["meta_tradeable"].astype(int))
                    sprob = model.predict_proba(sel[META_FEATURES])[:, 1]
                    meta_t, diagnostics = _choose_threshold(sel, sel["meta_pred_direction"].to_numpy(int), sprob)
                    tprob = model.predict_proba(testm[META_FEATURES])[:, 1]
            baseline_keep = np.isin(tp, [-1,1])
            candidate_keep = baseline_keep & (tprob >= meta_t)
            baseline_m = _metrics(testm, tp, baseline_keep)
            candidate_m = _metrics(testm, tp, candidate_keep)
            folds.append({
                "fold": fold_no, "test_day": test_day, "meta_threshold": meta_t,
                "validation_meta_diagnostics": diagnostics,
                "baseline": baseline_m, "candidate": candidate_m,
                "test_used_for_meta_fit_or_selection": False,
            })
    finally:
        v3_mod._expand = original_expand
    baseline = _aggregate(folds, "baseline"); candidate = _aggregate(folds, "candidate")
    report = {
        "version": VERSION, "generated_at": datetime.now(base.CN_TZ).isoformat(timespec="seconds"),
        "qualification": "DEVELOPMENT_BACKTEST_NOT_PRISTINE_OOS",
        "meta_features_predeclared": list(META_FEATURES), "meta_thresholds_predeclared": list(META_THRESHOLDS),
        "validation_signal_split": "first_60pct_fit_last_40pct_select", "test_used_for_selection": False,
        "meta_target": "exact_signed_execution_edge_gt_2bp", "same_v4r_direction_model": True,
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
    print(f"[META BASE] acc={b.get('directional_accuracy_pct')} cov={b.get('directional_coverage_pct')} net={b.get('avg_net_edge_bp')} n={b.get('directional_predictions')}")
    print(f"[META FILTER] acc={c.get('directional_accuracy_pct')} cov={c.get('directional_coverage_pct')} net={c.get('avg_net_edge_bp')} exact_net={c.get('avg_exact_exec_net_edge_bp')} n={c.get('directional_predictions')} gate={report['development_gate']['pass']}")
    print("[IMPORTANT] Historical pass may enter research/shadow only; production remains untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
