# -*- coding: utf-8 -*-
"""Asymmetric regime-aware L1/Tick 60s trainer V4.

Research-only design:
- two independent binary heads: UP-entry and DOWN-risk;
- fixed conflict arbitration combines the heads without test-set tuning;
- continuation/reversal and flow/price divergence features use only information
  available at prediction time;
- train rows are thinned to ~15s per symbol to reduce overlapping-label pseudo
  sample inflation; validation/test remain ~60s non-overlapping;
- each head chooses its probability threshold on validation net edge only;
- test is evaluated once, after thresholds are frozen;
- 2bp execution hurdle remains mandatory; nothing is auto-deployed.
"""
from __future__ import annotations

import argparse
import json
import math
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.utils.class_weight import compute_sample_weight

import services.train_l1_60s_model_v3 as v3
import services.train_l2_60s_model_v3 as splitbase

TRAINER_VERSION = "l1-60s-trainer-v4-asymmetric-regime-20260904"
DATA_ROOT = Path.home() / "AStockData"
MODEL_DIR = DATA_ROOT / "models" / "l1_60s"
RET_TARGET = v3.RET_TARGET
ACTION_TARGET = "label_actionable_smoothed_mid_60"
UP_TARGET = "target_up_entry_60"
DOWN_TARGET = "target_down_risk_60"
TRAIN_THIN_SECONDS = 15.0
CONFLICT_MARGIN = 0.05
PROB_THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
MIN_SHADOW_SIGNALS = 30
MIN_SHADOW_ACCURACY = 55.0
MIN_OPEN_TEST_N = 20
MIN_OPEN_SIGNALS = 8
MIN_OPEN_ACCURACY = 55.0

REGIME_FEATURES = [
    "continuation_strength", "reversal_strength", "momentum_curvature",
    "flow_bias", "flow_price_alignment", "flow_price_divergence",
    "vwap_stretch_abs_pct", "vwap_momentum_alignment",
    "extreme_proximity_pct", "high_low_asymmetry_pct",
    "spread_depth_stress", "microprice_momentum_alignment",
    "volume_impulse_ratio", "amount_impulse_ratio",
]
FEATURES = list(dict.fromkeys(list(v3.FEATURES) + REGIME_FEATURES))


def _num(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(default)


def _add_regime_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    c10, c30, c60, c120 = (_num(out, x) for x in (
        "change_10s_pct", "change_30s_pct", "change_60s_pct", "change_120s_pct"
    ))
    s10, s30, s60 = np.sign(c10), np.sign(c30), np.sign(c60)
    aligned = (s10 == s30) & (s30 == s60) & (s10 != 0)
    out["continuation_strength"] = np.where(
        aligned, c10.abs() + c30.abs() / 3.0 + c60.abs() / 6.0, 0.0
    )
    out["reversal_strength"] = np.where(
        (s10 * s60) < 0, c10.abs() + c60.abs() / 3.0 + c120.abs() / 8.0, 0.0
    )
    out["momentum_curvature"] = c10 - (c30 / 3.0) - (c30 - c60 / 2.0) / 2.0

    tick = (_num(out, "tick_buy_pct", 50.0) - 50.0) / 50.0
    book = (_num(out, "book_buy_pressure_pct", 50.0) - 50.0) / 50.0
    flow = (tick + book) / 2.0
    out["flow_bias"] = flow
    out["flow_price_alignment"] = flow * np.sign(c30)
    out["flow_price_divergence"] = -out["flow_price_alignment"]

    vwap = _num(out, "above_vwap_pct")
    out["vwap_stretch_abs_pct"] = vwap.abs()
    out["vwap_momentum_alignment"] = vwap * np.sign(c30)
    dh, dl = _num(out, "distance_high_pct"), _num(out, "distance_low_pct")
    out["extreme_proximity_pct"] = np.minimum(dh.abs(), dl.abs())
    out["high_low_asymmetry_pct"] = dl.abs() - dh.abs()

    spread_chg = _num(out, "spread_change_5s")
    depth_chg = _num(out, "depth5_imbalance_change_5s")
    out["spread_depth_stress"] = spread_chg.abs() * (1.0 + depth_chg.abs() / 100.0)
    out["microprice_momentum_alignment"] = _num(out, "microprice_vs_mid_pct") * np.sign(c10)

    vd = _num(out, "volume_delta_5s")
    va = _num(out, "volume_accel_5s")
    ad = _num(out, "amount_delta_5s")
    aa = _num(out, "amount_accel_5s")
    out["volume_impulse_ratio"] = va / (vd.abs() + 1.0)
    out["amount_impulse_ratio"] = aa / (ad.abs() + 1.0)

    for c in FEATURES:
        if c not in out.columns:
            out[c] = np.nan
        out[c] = pd.to_numeric(out[c], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return out


def _prepare(symbol: str, data_root: Path, hurdle_bp: float) -> pd.DataFrame:
    raw = v3._load(data_root)
    if raw.empty:
        return raw
    frame = _add_regime_features(v3._expand(raw))
    if symbol.upper() != "ALL":
        frame = frame[frame["symbol"].astype(str).str.upper() == symbol.upper()].copy()
    frame = frame.dropna(subset=[RET_TARGET, "generated_ts"]).sort_values("generated_ts").reset_index(drop=True)
    hurdle_pct = max(0.0, float(hurdle_bp)) / 100.0
    recorded = pd.to_numeric(frame.get("label_threshold_pct"), errors="coerce").fillna(0.01)
    effective = np.maximum(recorded.to_numpy(dtype=float), hurdle_pct)
    ret = frame[RET_TARGET].to_numpy(dtype=float)
    frame[ACTION_TARGET] = np.where(ret > effective, 1, np.where(ret < -effective, -1, 0)).astype(int)
    frame[UP_TARGET] = (ret > effective).astype(int)
    frame[DOWN_TARGET] = (ret < -effective).astype(int)
    frame["economic_hurdle_pct"] = effective
    return frame


def _thin(frame: pd.DataFrame, seconds: float) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    keep: List[int] = []
    last_by_symbol: Dict[str, float] = {}
    for idx, row in frame.sort_values("generated_ts").iterrows():
        symbol = str(row.get("symbol") or "")
        ts = float(row["generated_ts"])
        if ts - last_by_symbol.get(symbol, -1e18) >= seconds:
            keep.append(idx)
            last_by_symbol[symbol] = ts
    return frame.loc[keep].sort_values("generated_ts").copy()


def _models() -> Dict[str, Pipeline]:
    return {
        "logistic_balanced": Pipeline([
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(max_iter=3000, class_weight="balanced", C=0.35, solver="lbfgs")),
        ]),
        "hist_gradient_boosting": Pipeline([
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("model", HistGradientBoostingClassifier(
                learning_rate=0.035, max_iter=260, max_leaf_nodes=15,
                min_samples_leaf=40, l2_regularization=2.0, random_state=42,
            )),
        ]),
    }


def _fit(name: str, model: Pipeline, X: pd.DataFrame, y: np.ndarray) -> None:
    if len(np.unique(y)) < 2:
        raise ValueError("binary head has fewer than two train classes")
    if name == "hist_gradient_boosting":
        w = compute_sample_weight(class_weight="balanced", y=y)
        model.fit(X, y, model__sample_weight=w)
    else:
        model.fit(X, y)


def _positive_probability(model: Pipeline, X: pd.DataFrame) -> np.ndarray:
    proba = model.predict_proba(X)
    classes = np.asarray(model.classes_, dtype=int)
    pos = np.where(classes == 1)[0]
    if not len(pos):
        return np.zeros(len(X), dtype=float)
    return proba[:, int(pos[0])]


def _head_metrics(frame: pd.DataFrame, prob: np.ndarray, threshold: float, direction: int,
                  hurdle_bp: float, target_col: str) -> Dict[str, Any]:
    active = np.asarray(prob >= threshold)
    n = int(active.sum())
    if n:
        truth = frame[target_col].astype(int).to_numpy()[active]
        ret = frame[RET_TARGET].astype(float).to_numpy()[active]
        accuracy = float((truth == 1).mean() * 100.0)
        signed = (ret if direction == 1 else -ret) * 100.0
        gross = float(np.mean(signed))
    else:
        accuracy = gross = None
    return {
        "n": int(len(frame)), "directional_predictions": n,
        "directional_coverage_pct": 100.0 * n / len(frame) if len(frame) else None,
        "directional_accuracy_pct": accuracy,
        "avg_gross_edge_bp": gross,
        "execution_hurdle_bp": float(hurdle_bp),
        "avg_net_edge_bp": (gross - float(hurdle_bp)) if gross is not None else None,
    }


def _choose_head(model: Pipeline, val: pd.DataFrame, direction: int, hurdle_bp: float,
                 target_col: str) -> Tuple[float, Dict[str, Any], List[Dict[str, Any]]]:
    if val.empty:
        return 0.70, {}, []
    prob = _positive_probability(model, val[FEATURES])
    curve: List[Dict[str, Any]] = []
    for t in PROB_THRESHOLDS:
        m = _head_metrics(val, prob, t, direction, hurdle_bp, target_col)
        curve.append({"probability_threshold": t, **m})
    min_n = max(8, int(math.ceil(len(val) * 0.03)))
    eligible = [x for x in curve if int(x.get("directional_predictions") or 0) >= min_n]
    if not eligible:
        return 0.70, {}, curve
    def score(x: Dict[str, Any]) -> Tuple[float, float, int]:
        edge = float(x.get("avg_net_edge_bp") if x.get("avg_net_edge_bp") is not None else -1e9)
        acc = float(x.get("directional_accuracy_pct") or 0.0)
        n = int(x.get("directional_predictions") or 0)
        return edge * math.sqrt(max(1, n)), acc, n
    best = max(eligible, key=score)
    return float(best["probability_threshold"]), best, curve


def _combine(up_prob: np.ndarray, down_prob: np.ndarray, up_t: float, down_t: float) -> np.ndarray:
    up_on, down_on = up_prob >= up_t, down_prob >= down_t
    pred = np.zeros(len(up_prob), dtype=int)
    pred[up_on & ~down_on] = 1
    pred[down_on & ~up_on] = -1
    both = up_on & down_on
    if both.any():
        up_margin = (up_prob[both] - up_t) / max(1e-9, 1.0 - up_t)
        dn_margin = (down_prob[both] - down_t) / max(1e-9, 1.0 - down_t)
        gap = up_margin - dn_margin
        idx = np.where(both)[0]
        pred[idx[gap > CONFLICT_MARGIN]] = 1
        pred[idx[gap < -CONFLICT_MARGIN]] = -1
    return pred


def _combined_metrics(frame: pd.DataFrame, pred: np.ndarray, hurdle_bp: float) -> Dict[str, Any]:
    y = frame[ACTION_TARGET].astype(int).to_numpy()
    ret = frame[RET_TARGET].astype(float).to_numpy()
    active = np.isin(pred, [-1, 1]) & np.isfinite(ret)
    n = int(active.sum())
    if n:
        acc = float((pred[active] == y[active]).mean() * 100.0)
        signed = np.where(pred[active] == 1, ret[active], -ret[active]) * 100.0
        gross = float(np.mean(signed))
        up_n = int((pred[active] == 1).sum())
        down_n = int((pred[active] == -1).sum())
    else:
        acc = gross = None
        up_n = down_n = 0
    return {
        "n": int(len(frame)), "directional_predictions": n,
        "directional_coverage_pct": 100.0 * n / len(frame) if len(frame) else None,
        "directional_accuracy_pct": acc,
        "up_predictions": up_n, "down_predictions": down_n,
        "avg_gross_edge_bp": gross, "execution_hurdle_bp": float(hurdle_bp),
        "avg_net_edge_bp": (gross - float(hurdle_bp)) if gross is not None else None,
    }


def _segment(frame: pd.DataFrame, lo: float, hi: float) -> pd.DataFrame:
    minute = _num(frame, "minute_of_day", -1.0)
    return frame[(minute >= lo) & (minute < hi)].copy()


def _evaluate_combined(up_model: Pipeline, down_model: Pipeline, frame: pd.DataFrame,
                       up_t: float, down_t: float, hurdle_bp: float) -> Dict[str, Any]:
    if frame.empty:
        return {"n": 0, "directional_predictions": 0}
    up_p = _positive_probability(up_model, frame[FEATURES])
    dn_p = _positive_probability(down_model, frame[FEATURES])
    return _combined_metrics(frame, _combine(up_p, dn_p, up_t, down_t), hurdle_bp)


def _readiness(meta: Dict[str, Any], combined: Dict[str, Any], opening: Dict[str, Any]) -> Dict[str, Any]:
    reasons: List[str] = []
    if str(meta.get("maturity")) != "STANDARD": reasons.append("need_at_least_3_trade_days")
    if int(combined.get("directional_predictions") or 0) < MIN_SHADOW_SIGNALS: reasons.append("too_few_directional_test_signals")
    if float(combined.get("directional_accuracy_pct") or 0.0) < MIN_SHADOW_ACCURACY: reasons.append("directional_accuracy_below_55")
    if float(combined.get("avg_net_edge_bp") if combined.get("avg_net_edge_bp") is not None else -1e9) <= 0.0: reasons.append("net_edge_not_positive")
    if int(opening.get("n") or 0) < MIN_OPEN_TEST_N: reasons.append("opening_hour_test_too_small")
    if int(opening.get("directional_predictions") or 0) < MIN_OPEN_SIGNALS: reasons.append("opening_hour_too_few_signals")
    if float(opening.get("directional_accuracy_pct") or 0.0) < MIN_OPEN_ACCURACY: reasons.append("opening_hour_accuracy_below_55")
    if float(opening.get("avg_net_edge_bp") if opening.get("avg_net_edge_bp") is not None else -1e9) <= 0.0: reasons.append("opening_hour_net_edge_not_positive")
    return {"eligible_for_shadow_review": not reasons, "eligible_for_live_deployment": False, "reasons": reasons}


def train(symbol: str, min_samples: int, data_root: Path, hurdle_bp: float) -> int:
    try:
        frame = _prepare(symbol, data_root, hurdle_bp)
        if frame.empty:
            print("V4_STAGE=load NO_DATA")
            return 2
        if len(frame) < min_samples:
            print(f"V4_STAGE=filter NEED_SAMPLES found={len(frame)} required={min_samples}")
            return 2
        train_df, val_df, test_df, meta = splitbase._split(frame)
        if train_df.empty or test_df.empty:
            print("V4_STAGE=split EMPTY_TRAIN_OR_TEST")
            return 2
        train_fit = _thin(train_df, TRAIN_THIN_SECONDS)
        non_val = splitbase._nonoverlap(val_df) if not val_df.empty else val_df.copy()
        non_test = splitbase._nonoverlap(test_df)
        if train_fit.empty or non_test.empty:
            print("V4_STAGE=thin EMPTY_TRAIN_OR_TEST")
            return 2

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        scope = symbol.upper().replace(".", "_")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report: Dict[str, Any] = {
            "trainer_version": TRAINER_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "scope": symbol.upper(), "data_mode": "L1_BASELINE_ASYMMETRIC",
            "target": ACTION_TARGET, "return_target": RET_TARGET,
            "execution_hurdle_bp": float(hurdle_bp), **meta,
            "samples_total": int(len(frame)), "samples_train_dense": int(len(train_df)),
            "samples_train_thinned_15s": int(len(train_fit)),
            "samples_validation_nonoverlap": int(len(non_val)),
            "samples_test_nonoverlap": int(len(non_test)),
            "features": FEATURES, "regime_features": REGIME_FEATURES,
            "architecture": "TWO_BINARY_HEADS_UP_ENTRY_AND_DOWN_RISK",
            "conflict_policy": f"fixed_normalized_margin_gap>{CONFLICT_MARGIN}",
            "models": {}, "test_threshold_search_performed": False,
            "auto_deployed": False, "eligible_for_live_deployment": False,
        }

        for family in ("logistic_balanced", "hist_gradient_boosting"):
            up_model = _models()[family]
            down_model = _models()[family]
            _fit(family, up_model, train_fit[FEATURES], train_fit[UP_TARGET].astype(int).to_numpy())
            _fit(family, down_model, train_fit[FEATURES], train_fit[DOWN_TARGET].astype(int).to_numpy())

            up_t, up_val, up_curve = _choose_head(up_model, non_val, 1, hurdle_bp, UP_TARGET)
            dn_t, dn_val, dn_curve = _choose_head(down_model, non_val, -1, hurdle_bp, DOWN_TARGET)
            up_test_prob = _positive_probability(up_model, non_test[FEATURES])
            dn_test_prob = _positive_probability(down_model, non_test[FEATURES])
            up_test = _head_metrics(non_test, up_test_prob, up_t, 1, hurdle_bp, UP_TARGET)
            dn_test = _head_metrics(non_test, dn_test_prob, dn_t, -1, hurdle_bp, DOWN_TARGET)
            combined_pred = _combine(up_test_prob, dn_test_prob, up_t, dn_t)
            combined = _combined_metrics(non_test, combined_pred, hurdle_bp)

            tod = {
                "OPEN_CORE_0930_1030": _evaluate_combined(up_model, down_model, _segment(non_test, 570, 630), up_t, dn_t, hurdle_bp),
                "AM_LATE_1030_1130": _evaluate_combined(up_model, down_model, _segment(non_test, 630, 690), up_t, dn_t, hurdle_bp),
                "PM_1300_1500": _evaluate_combined(up_model, down_model, _segment(non_test, 780, 900), up_t, dn_t, hurdle_bp),
            }
            per_symbol: Dict[str, Any] = {}
            for sym, part in non_test.groupby("symbol"):
                per_symbol[str(sym)] = _evaluate_combined(up_model, down_model, part, up_t, dn_t, hurdle_bp)

            path = MODEL_DIR / f"{scope}_{family}_asymmetric_{stamp}.joblib"
            joblib.dump({
                "up_model": up_model, "down_model": down_model, "features": FEATURES,
                "up_threshold": up_t, "down_threshold": dn_t,
                "scope": symbol.upper(), "trainer_version": TRAINER_VERSION,
                "execution_hurdle_bp": float(hurdle_bp), "conflict_margin": CONFLICT_MARGIN,
            }, path)
            item = {
                "selected_probability_threshold": {"up_entry": up_t, "down_risk": dn_t},
                "threshold_source": "VALIDATION_NONOVERLAP",
                "heads": {
                    "up_entry": {"selected_validation_point": up_val, "validation_curve": up_curve, "test_nonoverlap": up_test},
                    "down_risk": {"selected_validation_point": dn_val, "validation_curve": dn_curve, "test_nonoverlap": dn_test},
                },
                "selected_threshold_test_nonoverlap": combined,
                "time_of_day_test_nonoverlap": tod,
                "test_nonoverlap_by_symbol": per_symbol,
                "model_path": str(path),
            }
            item["readiness"] = _readiness(meta, combined, tod["OPEN_CORE_0930_1030"])
            report["models"][family] = item

        report["any_model_eligible_for_shadow_review"] = any(
            bool(x.get("readiness", {}).get("eligible_for_shadow_review")) for x in report["models"].values()
        )
        report_path = MODEL_DIR / f"{scope}_training_report_latest.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"V4_SUCCESS scope={symbol.upper()} samples={len(frame)} train15s={len(train_fit)} test60s={len(non_test)}")
        for family, item in report["models"].items():
            c = item["selected_threshold_test_nonoverlap"]
            u = item["heads"]["up_entry"]["test_nonoverlap"]
            d = item["heads"]["down_risk"]["test_nonoverlap"]
            print(f"{family}: combined acc={c.get('directional_accuracy_pct')} n={c.get('directional_predictions')} net={c.get('avg_net_edge_bp')}bp")
            print(f"  UP acc={u.get('directional_accuracy_pct')} n={u.get('directional_predictions')} net={u.get('avg_net_edge_bp')}bp threshold={item['selected_probability_threshold']['up_entry']}")
            print(f"  DOWN acc={d.get('directional_accuracy_pct')} n={d.get('directional_predictions')} net={d.get('avg_net_edge_bp')}bp threshold={item['selected_probability_threshold']['down_risk']}")
        print(f"Report: {report_path}")
        print("Research only. Validation selected head thresholds; test was never searched.")
        return 0
    except Exception as exc:
        print(f"V4_EXCEPTION {type(exc).__name__}: {exc}\n{traceback.format_exc()[-7000:]}")
        return 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="301236.SZ")
    p.add_argument("--min-samples", type=int, default=200)
    p.add_argument("--data-root", default=str(DATA_ROOT))
    p.add_argument("--hurdle-bp", type=float, default=2.0)
    a = p.parse_args()
    raise SystemExit(train(a.symbol, a.min_samples, Path(a.data_root).expanduser(), a.hurdle_bp))


if __name__ == "__main__":
    main()
