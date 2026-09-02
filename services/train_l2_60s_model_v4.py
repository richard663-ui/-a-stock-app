# -*- coding: utf-8 -*-
"""V4 research-only 60s trainer: no test cherry-picking, cost-aware targets.

Key rules:
- +60s smoothed mid remains the return target;
- actionable UP/DOWN labels require movement beyond max(recorded noise band,
  configured execution hurdle);
- probability threshold is selected ONLY on validation data, then frozen on test;
- with no validation day, a fixed conservative threshold is used;
- test reports gross and net edge, probability calibration and per-symbol results;
- no model is ever auto-deployed.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score, confusion_matrix, f1_score

import services.train_l2_60s_model_v3 as base

TRAINER_VERSION = "l2-60s-trainer-v4-validation-cost-stability-20260902"
ACTION_TARGET = "label_actionable_smoothed_mid_60"
DEFAULT_HURDLE_BP = 2.0
DEFAULT_NO_VALIDATION_THRESHOLD = 0.65
MIN_SHADOW_TEST_N = 30
MIN_SHADOW_DIRECTIONAL_N = 20

EXTRA_FEATURES = [
    "market_hs300_return_pct", "market_chinext_return_pct",
    "relative_to_hs300_pct", "relative_to_chinext_pct",
    "l2_quote_age_s", "l2_transaction_age_s", "l2_order_age_s", "l2_orderqueue_age_s",
    "l2_core_max_age_s", "l2_core_fresh",
]
FEATURES = list(dict.fromkeys(list(base.FEATURES) + EXTRA_FEATURES))


def _to_actionable(frame: pd.DataFrame, hurdle_bp: float) -> pd.DataFrame:
    out = frame.copy()
    ret = pd.to_numeric(out[base.RET_TARGET], errors="coerce")
    recorded = pd.to_numeric(out.get("label_threshold_pct"), errors="coerce").fillna(0.01)
    hurdle_pct = max(0.0, float(hurdle_bp)) / 100.0
    effective = np.maximum(recorded.to_numpy(dtype=float), hurdle_pct)
    target = np.where(ret.to_numpy(dtype=float) > effective, 1,
                      np.where(ret.to_numpy(dtype=float) < -effective, -1, 0))
    out[ACTION_TARGET] = target.astype(int)
    out["economic_hurdle_pct"] = effective
    for c in FEATURES:
        if c not in out.columns:
            out[c] = np.nan
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _edge(y: np.ndarray, pred: np.ndarray, ret: np.ndarray, hurdle_bp: float) -> Dict[str, Any]:
    vals: List[float] = []
    correct = 0
    for yt, yp, r in zip(y, pred, ret):
        yp = int(yp)
        if yp not in (-1, 1) or not math.isfinite(float(r)):
            continue
        gross_bp = (float(r) if yp == 1 else -float(r)) * 100.0
        vals.append(gross_bp)
        correct += int(int(yt) == yp)
    n = len(vals)
    gross = float(np.mean(vals)) if vals else None
    net = (gross - float(hurdle_bp)) if gross is not None else None
    return {
        "directional_predictions": n,
        "directional_coverage_pct": 100.0 * n / len(pred) if len(pred) else None,
        "directional_accuracy_pct": 100.0 * correct / n if n else None,
        "avg_gross_edge_bp": gross,
        "execution_hurdle_bp": float(hurdle_bp),
        "avg_net_edge_bp": net,
    }


def _metrics(name: str, frame: pd.DataFrame, pred: np.ndarray, hurdle_bp: float) -> Dict[str, Any]:
    y = frame[ACTION_TARGET].astype(int).to_numpy()
    ret = frame[base.RET_TARGET].astype(float).to_numpy()
    return {
        "model": name,
        "n": int(len(frame)),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)) if len(y) else None,
        "macro_f1": float(f1_score(y, pred, average="macro", zero_division=0)) if len(y) else None,
        "confusion_matrix_labels_-1_0_1": confusion_matrix(y, pred, labels=[-1, 0, 1]).tolist() if len(y) else [],
        "true_classes": {str(k): int(v) for k, v in pd.Series(y).value_counts().sort_index().items()},
        "pred_classes": {str(k): int(v) for k, v in pd.Series(pred).value_counts().sort_index().items()},
        **_edge(y, pred, ret, hurdle_bp),
    }


def _selective(model, frame: pd.DataFrame, threshold: float, hurdle_bp: float) -> Dict[str, Any]:
    if frame.empty:
        return {"probability_threshold": threshold, "directional_predictions": 0}
    pred, _ = base._predict_selective(model, frame[FEATURES], threshold)
    return {"probability_threshold": threshold, **_metrics("selective", frame, pred, hurdle_bp)}


def _validation_curve(model, frame: pd.DataFrame, hurdle_bp: float) -> List[Dict[str, Any]]:
    return [_selective(model, frame, t, hurdle_bp) for t in base.PROB_THRESHOLDS] if not frame.empty else []


def _choose_threshold(curve: List[Dict[str, Any]], n_validation: int) -> Tuple[float, str, Dict[str, Any]]:
    if not curve:
        return DEFAULT_NO_VALIDATION_THRESHOLD, "PRESET_NO_VALIDATION", {}
    min_n = max(5, int(math.ceil(max(1, n_validation) * 0.10)))
    eligible = [x for x in curve if int(x.get("directional_predictions") or 0) >= min_n]
    if not eligible:
        return DEFAULT_NO_VALIDATION_THRESHOLD, "PRESET_INSUFFICIENT_VALIDATION_SIGNALS", {}
    def score(x: Dict[str, Any]) -> Tuple[float, float, int]:
        edge = float(x.get("avg_net_edge_bp") if x.get("avg_net_edge_bp") is not None else -1e9)
        acc = float(x.get("directional_accuracy_pct") or 0.0)
        n = int(x.get("directional_predictions") or 0)
        return edge * math.sqrt(max(1, n)), acc, n
    best = max(eligible, key=score)
    return float(best["probability_threshold"]), "VALIDATION_NONOVERLAP", best


def _calibration(model, frame: pd.DataFrame) -> Dict[str, Any]:
    if frame.empty:
        return {}
    proba = model.predict_proba(frame[FEATURES])
    classes = np.asarray(model.classes_, dtype=int)
    y = frame[ACTION_TARGET].astype(int).to_numpy()
    best_idx = np.argmax(proba, axis=1)
    conf = proba[np.arange(len(proba)), best_idx]
    pred = classes[best_idx]
    hit = (pred == y).astype(float)
    bins = []
    ece = 0.0
    for lo, hi in ((0.0,0.4),(0.4,0.5),(0.5,0.6),(0.6,0.7),(0.7,0.8),(0.8,0.9),(0.9,1.000001)):
        mask = (conf >= lo) & (conf < hi)
        n = int(mask.sum())
        if not n:
            continue
        c = float(conf[mask].mean())
        a = float(hit[mask].mean())
        ece += (n / len(conf)) * abs(c - a)
        bins.append({"lo": lo, "hi": hi, "n": n, "mean_confidence": c, "empirical_accuracy": a})
    onehot = np.zeros_like(proba)
    for i, cls in enumerate(classes):
        onehot[:, i] = (y == cls).astype(float)
    brier = float(np.mean(np.sum((proba - onehot) ** 2, axis=1)))
    return {"ece": float(ece), "multiclass_brier": brier, "bins": bins}


def _baseline(frame: pd.DataFrame, hurdle_bp: float) -> Dict[str, Any]:
    mapping = {"DOWN": -1, "WATCH": 0, "UP": 1}
    pred = frame["baseline_direction"].map(mapping).fillna(0).astype(int).to_numpy()
    return _metrics("direction_v18_baseline", frame, pred, hurdle_bp)


def _per_symbol(model, frame: pd.DataFrame, threshold: float, hurdle_bp: float) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if frame.empty:
        return out
    for symbol, part in frame.groupby("symbol"):
        pred, _ = base._predict_selective(model, part[FEATURES], threshold)
        out[str(symbol)] = _metrics("selective", part, pred, hurdle_bp)
    return out


def _readiness(report: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    test = item.get("selected_threshold_test_nonoverlap") or {}
    reasons: List[str] = []
    if report.get("maturity") != "STANDARD": reasons.append("need_at_least_3_trade_days")
    if int(report.get("samples_test_nonoverlap") or 0) < MIN_SHADOW_TEST_N: reasons.append("test_nonoverlap_too_small")
    if item.get("threshold_source") != "VALIDATION_NONOVERLAP": reasons.append("threshold_not_selected_on_validation")
    if int(test.get("directional_predictions") or 0) < MIN_SHADOW_DIRECTIONAL_N: reasons.append("too_few_directional_test_signals")
    if float(test.get("directional_accuracy_pct") or 0.0) < 55.0: reasons.append("directional_accuracy_below_55")
    if float(test.get("avg_net_edge_bp") if test.get("avg_net_edge_bp") is not None else -1e9) <= 0.0: reasons.append("net_edge_not_positive")
    return {"eligible_for_shadow_review": not reasons, "reasons": reasons, "eligible_for_live_deployment": False}


def train(symbol: str, min_samples: int, data_root: Path, hurdle_bp: float) -> int:
    base.FEATURES = FEATURES
    raw = base._load_rows(data_root)
    if raw.empty:
        print("No training samples yet.")
        return 2
    frame = base._expand(raw)
    frame = _to_actionable(frame, hurdle_bp)
    frame = frame[(frame["valid"] == 1) & (frame["true_l2"] == 1) & frame[ACTION_TARGET].isin([-1,0,1])].copy()
    if symbol.upper() != "ALL":
        frame = frame[frame["symbol"].str.upper() == symbol.upper()].copy()
    frame = frame.dropna(subset=[ACTION_TARGET, base.RET_TARGET, "generated_ts"]).sort_values("generated_ts").reset_index(drop=True)
    if len(frame) < min_samples:
        print(f"Need >= {min_samples} valid fresh-L2 labels; found {len(frame)} for {symbol.upper()}.")
        return 2

    original_target = base.TARGET
    base.TARGET = ACTION_TARGET
    try:
        train_df, val_df, test_df, split_meta = base._split(frame)
        if train_df.empty or test_df.empty:
            print("Not enough chronological blocks after purge.")
            return 2
        y_train = train_df[ACTION_TARGET].astype(int).to_numpy()
        if len(set(y_train.tolist())) < 2:
            print("Training block has fewer than two actionable classes.")
            return 2
        dense_test = test_df.copy(); nonoverlap_test = base._nonoverlap(test_df)
        dense_val = val_df.copy(); nonoverlap_val = base._nonoverlap(val_df) if not val_df.empty else val_df.copy()

        base.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        scope = symbol.upper().replace(".", "_")
        report: Dict[str, Any] = {
            "trainer_version": TRAINER_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "scope": symbol.upper(), "target": ACTION_TARGET,
            "return_target": base.RET_TARGET, "execution_hurdle_bp": float(hurdle_bp),
            **split_meta,
            "samples_total": len(frame), "samples_train": len(train_df),
            "samples_validation_dense": len(dense_val), "samples_validation_nonoverlap": len(nonoverlap_val),
            "samples_test_dense": len(dense_test), "samples_test_nonoverlap": len(nonoverlap_test),
            "features": FEATURES, "feature_missing_pct": base._missingness(frame),
            "baseline_test_nonoverlap": _baseline(nonoverlap_test, hurdle_bp),
            "models": {}, "test_threshold_search_performed": False,
            "auto_deployed": False, "eligible_for_live_deployment": False,
        }

        for name, model in base._models().items():
            base._fit_model(name, model, train_df[FEATURES], y_train)
            dense_pred = model.predict(dense_test[FEATURES])
            non_pred = model.predict(nonoverlap_test[FEATURES])
            val_curve = _validation_curve(model, nonoverlap_val, hurdle_bp)
            threshold, source, selected_val = _choose_threshold(val_curve, len(nonoverlap_val))
            selected_test_pred, _ = base._predict_selective(model, nonoverlap_test[FEATURES], threshold)
            item: Dict[str, Any] = {
                "test_dense_unselective": _metrics(name, dense_test, dense_pred, hurdle_bp),
                "test_nonoverlap_unselective": _metrics(name, nonoverlap_test, non_pred, hurdle_bp),
                "validation_nonoverlap_curve": val_curve,
                "selected_probability_threshold": threshold,
                "threshold_source": source,
                "selected_validation_point": selected_val,
                "selected_threshold_test_nonoverlap": _metrics(name, nonoverlap_test, selected_test_pred, hurdle_bp),
                "calibration_test_nonoverlap": _calibration(model, nonoverlap_test),
                "test_nonoverlap_by_symbol": _per_symbol(model, nonoverlap_test, threshold, hurdle_bp),
            }
            path = base.MODEL_DIR / f"{scope}_{name}_{stamp}.joblib"
            joblib.dump({"model": model, "features": FEATURES, "target": ACTION_TARGET,
                         "scope": symbol.upper(), "trainer_version": TRAINER_VERSION,
                         "selected_probability_threshold": threshold, "threshold_source": source,
                         "execution_hurdle_bp": float(hurdle_bp), "split_meta": split_meta}, path)
            item["model_path"] = str(path)
            item["readiness"] = _readiness(report, item)
            report["models"][name] = item

        report["any_model_eligible_for_shadow_review"] = any(
            bool(x.get("readiness",{}).get("eligible_for_shadow_review")) for x in report["models"].values()
        )
        report_path = base.MODEL_DIR / f"{scope}_training_report_latest.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("=" * 86)
        print(f"AStock L2 60s ML V4 | scope={symbol.upper()} | samples={len(frame)} | hurdle={hurdle_bp:.2f}bp")
        print(split_meta["protocol"])
        for name, item in report["models"].items():
            t = item["selected_threshold_test_nonoverlap"]
            print(f"{name}: threshold={item['selected_probability_threshold']:.2f} source={item['threshold_source']} "
                  f"n={t.get('directional_predictions')} acc={t.get('directional_accuracy_pct')} "
                  f"gross={t.get('avg_gross_edge_bp')}bp net={t.get('avg_net_edge_bp')}bp")
            print(f"  readiness={item['readiness']}")
        print(f"Report: {report_path}")
        print("Research only. Test data never selected a threshold. Nothing was deployed.")
        return 0
    finally:
        base.TARGET = original_target


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=base.PRIMARY_SYMBOL)
    p.add_argument("--min-samples", type=int, default=200)
    p.add_argument("--data-root", default=str(base.DATA_ROOT))
    p.add_argument("--hurdle-bp", type=float, default=DEFAULT_HURDLE_BP)
    a = p.parse_args()
    raise SystemExit(train(a.symbol, a.min_samples, Path(a.data_root).expanduser(), a.hurdle_bp))


if __name__ == "__main__":
    main()
