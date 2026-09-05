# -*- coding: utf-8 -*-
"""Execution-aligned L1/Tick 60s challenger V6.

CHALLENGER ONLY. V4R remains Champion and V5R remains the conservative control.

This version intentionally changes only three structural items that were exposed
by the frozen QMT historical audit, without loosening validation gates or tuning
against historical test days:
1) UP-entry is labelled/evaluated on an ask-now -> future-bid proxy rather than
   future mid alone. DOWN-risk is labelled/evaluated on bid-now -> future-bid
   proxy. The proxy uses the observed entry spread and smoothed +60s future mid,
   so it is available in both historical replay and the existing live recorder.
2) pooled ALL models receive fixed one-hot stock intercept features for the
   stable eight-name basket instead of pretending every stock has one baseline.
3) Logistic uses RobustScaler, keeping V5's C=0.12 and its validation-only robust
   stability gate unchanged. HGB remains control-only.

No test day chooses features, thresholds, regularization or gates. No auto
promotion/deployment is possible. Historical results remain development-only;
prospective unseen sessions are mandatory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

import services.train_l1_60s_model_v4 as core
import services.train_l1_60s_model_v4r as v4r
import services.train_l1_60s_model_v5_challenger as v5
import services.train_l2_60s_model_v3 as splitbase

TRAINER_VERSION = "l1-60s-trainer-v6-exec-aligned-stock-intercept-robust-20260905"
DATA_ROOT = core.DATA_ROOT
MODEL_DIR = core.MODEL_DIR / "v6_exec_aligned"
CHAMPION_VERSION = v4r.TRAINER_VERSION
STABLE_SYMBOLS = (
    "301236.SZ", "300308.SZ", "000400.SZ", "600522.SH",
    "601179.SH", "600105.SH", "002916.SZ", "000811.SZ",
)
SYMBOL_FEATURES = ["stock_" + x.replace(".", "_") for x in STABLE_SYMBOLS]
FEATURES = list(dict.fromkeys(list(core.FEATURES) + SYMBOL_FEATURES))
UP_EXEC_RET = "ret_up_ask_to_future_bid_proxy_60_pct"
DOWN_HOLD_RET = "ret_down_bid_to_future_bid_proxy_60_pct"
TARGET_POLICY = "UP=ask_now_to_future_bid_proxy;DOWN=bid_now_to_future_bid_proxy;future_bid_proxy=smoothed_future_mid_minus_current_half_spread"


def _models() -> Dict[str, Pipeline]:
    # Keep V5's Logistic regularization constant. Only the scaler is made robust
    # to heavy tails/outliers seen in the parity audit. HGB is control-only.
    logistic = Pipeline([
        ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scale", RobustScaler(quantile_range=(25.0, 75.0))),
        ("model", LogisticRegression(
            max_iter=3000, class_weight="balanced", C=0.12, solver="lbfgs"
        )),
    ])
    hgb = Pipeline([
        ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("model", HistGradientBoostingClassifier(
            learning_rate=0.025, max_iter=180, max_leaf_nodes=7,
            min_samples_leaf=80, l2_regularization=5.0, random_state=42,
        )),
    ])
    return {"logistic_balanced": logistic, "hist_gradient_boosting": hgb}


def _series(frame: pd.DataFrame, name: str, default: float = np.nan) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan)


def _prepare_exec_aligned(symbol: str, data_root: Path, hurdle_bp: float) -> pd.DataFrame:
    # _ORIGINAL_PREPARE is assigned before this function is installed as a patch.
    frame = _ORIGINAL_PREPARE(symbol, data_root, hurdle_bp)
    if frame.empty:
        return frame
    frame = frame.copy()

    sym = frame.get("symbol", pd.Series("", index=frame.index)).astype(str).str.upper()
    for raw, feature in zip(STABLE_SYMBOLS, SYMBOL_FEATURES):
        frame[feature] = (sym == raw).astype(float)

    mid = _series(frame, "mid_price")
    ask = _series(frame, "ask1")
    bid = _series(frame, "bid1")
    spread = _series(frame, "spread_pct").clip(lower=0.0)
    future_mid_ret = _series(frame, core.RET_TARGET)
    future_mid = mid * (1.0 + future_mid_ret / 100.0)
    # spread_pct is percentage units: 0.03 means 3bp, so /200 is half spread
    # as a decimal fraction. This is the same conservative proxy family already
    # reported by V5, now promoted into the TRAINING TARGET rather than report-only.
    future_bid_proxy = future_mid * (1.0 - spread / 200.0)

    valid_book = (mid > 0) & (ask > 0) & (bid > 0) & (ask >= bid) & future_mid.notna()
    up_ret = pd.Series(np.nan, index=frame.index, dtype=float)
    dn_ret = pd.Series(np.nan, index=frame.index, dtype=float)
    up_ret.loc[valid_book] = (future_bid_proxy.loc[valid_book] / ask.loc[valid_book] - 1.0) * 100.0
    dn_ret.loc[valid_book] = (future_bid_proxy.loc[valid_book] / bid.loc[valid_book] - 1.0) * 100.0
    frame[UP_EXEC_RET] = up_ret
    frame[DOWN_HOLD_RET] = dn_ret

    hurdle_pct = max(0.0, float(hurdle_bp)) / 100.0
    frame = frame.dropna(subset=[UP_EXEC_RET, DOWN_HOLD_RET, "generated_ts"]).copy()
    frame[core.UP_TARGET] = (_series(frame, UP_EXEC_RET) > hurdle_pct).astype(int)
    frame[core.DOWN_TARGET] = (_series(frame, DOWN_HOLD_RET) < -hurdle_pct).astype(int)
    # These events cannot logically overlap with a valid book, but keep an
    # explicit conflict fallback so malformed data cannot create both labels.
    up = frame[core.UP_TARGET].astype(int).to_numpy()
    dn = frame[core.DOWN_TARGET].astype(int).to_numpy()
    frame[core.ACTION_TARGET] = np.where((up == 1) & (dn == 0), 1,
                                  np.where((dn == 1) & (up == 0), -1, 0)).astype(int)
    frame["economic_hurdle_pct"] = hurdle_pct
    for c in FEATURES:
        if c not in frame.columns:
            frame[c] = np.nan
        frame[c] = pd.to_numeric(frame[c], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return frame.sort_values("generated_ts").reset_index(drop=True)


def _head_metrics_exec(frame: pd.DataFrame, prob: np.ndarray, threshold: float,
                       direction: int, hurdle_bp: float, target_col: str) -> Dict[str, Any]:
    active = np.asarray(prob >= threshold)
    n = int(active.sum())
    if n:
        truth = frame[target_col].astype(int).to_numpy()[active]
        if direction == 1:
            economic = _series(frame, UP_EXEC_RET).to_numpy(float)[active] * 100.0
        else:
            economic = -_series(frame, DOWN_HOLD_RET).to_numpy(float)[active] * 100.0
        finite = np.isfinite(economic)
        accuracy = float((truth[finite] == 1).mean() * 100.0) if finite.any() else None
        gross = float(np.mean(economic[finite])) if finite.any() else None
    else:
        accuracy = gross = None
    return {
        "n": int(len(frame)), "directional_predictions": n,
        "directional_coverage_pct": 100.0 * n / len(frame) if len(frame) else None,
        "directional_accuracy_pct": accuracy,
        "avg_gross_edge_bp": gross,
        "execution_hurdle_bp": float(hurdle_bp),
        "avg_net_edge_bp": (gross - float(hurdle_bp)) if gross is not None else None,
        "economic_return": UP_EXEC_RET if direction == 1 else DOWN_HOLD_RET,
    }


def _combined_metrics_exec(frame: pd.DataFrame, pred: np.ndarray, hurdle_bp: float) -> Dict[str, Any]:
    pred = np.asarray(pred, dtype=int)
    up_ret = _series(frame, UP_EXEC_RET).to_numpy(float)
    dn_ret = _series(frame, DOWN_HOLD_RET).to_numpy(float)
    active = np.isin(pred, [-1, 1])
    finite = active & np.where(pred == 1, np.isfinite(up_ret), np.isfinite(dn_ret))
    n = int(finite.sum())
    if n:
        truth_up = frame[core.UP_TARGET].astype(int).to_numpy()
        truth_dn = frame[core.DOWN_TARGET].astype(int).to_numpy()
        correct = np.where(pred == 1, truth_up == 1, truth_dn == 1)
        acc = float(correct[finite].mean() * 100.0)
        economic = np.where(pred == 1, up_ret, -dn_ret) * 100.0
        gross = float(np.mean(economic[finite]))
        up_n = int(((pred == 1) & finite).sum())
        down_n = int(((pred == -1) & finite).sum())
    else:
        acc = gross = None
        up_n = down_n = 0
    return {
        "n": int(len(frame)), "directional_predictions": n,
        "directional_coverage_pct": 100.0 * n / len(frame) if len(frame) else None,
        "directional_accuracy_pct": acc,
        "up_predictions": up_n, "down_predictions": down_n,
        "avg_gross_edge_bp": gross,
        "execution_hurdle_bp": float(hurdle_bp),
        "avg_net_edge_bp": (gross - float(hurdle_bp)) if gross is not None else None,
        "economic_metric_policy": TARGET_POLICY,
    }


def _annotate_report(symbol: str) -> None:
    scope = symbol.upper().replace(".", "_")
    path = MODEL_DIR / f"{scope}_training_report_latest.json"
    if not path.exists():
        return
    obj: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    obj.update({
        "trainer_version": TRAINER_VERSION,
        "candidate_role": "V6_CHALLENGER_ONLY",
        "champion_reference": CHAMPION_VERSION,
        "target_policy": TARGET_POLICY,
        "up_return_target": UP_EXEC_RET,
        "down_return_target": DOWN_HOLD_RET,
        "feature_policy": "V4R features + fixed eight-stock one-hot intercepts; no new market factor mining",
        "scaler_policy": "RobustScaler_IQR; V5 Logistic regularization C=0.12 unchanged",
        "threshold_policy": "V5 validation-only robust stability gate unchanged",
        "test_used_for_selection": False,
        "historical_backtest_can_promote": False,
        "requires_prospective_unseen_sessions": True,
        "auto_promoted": False,
        "eligible_for_live_deployment": False,
    })
    obj["features"] = FEATURES
    for name, item in (obj.get("models") or {}).items():
        item["candidate_role"] = "PROMOTION_CANDIDATE" if name == "logistic_balanced" else "CONTROL_ONLY"
        item["readiness"] = {
            "eligible_for_shadow_review": False,
            "eligible_for_live_deployment": False,
            "reasons": ["new_v6_challenger", "requires_prospective_unseen_sessions"],
        }
    obj["any_model_eligible_for_shadow_review"] = False
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


_ORIGINAL_PREPARE = core._prepare


def train(symbol: str, min_samples: int, data_root: Path, hurdle_bp: float) -> int:
    old = {
        "model_dir": core.MODEL_DIR,
        "version": core.TRAINER_VERSION,
        "features": core.FEATURES,
        "models": core._models,
        "prepare": core._prepare,
        "head_metrics": core._head_metrics,
        "combined_metrics": core._combined_metrics,
        "choose": core._choose_head,
        "thin": core._thin,
        "split": splitbase._split,
        "v5_features": v5.FEATURES,
    }
    try:
        core.MODEL_DIR = MODEL_DIR
        core.TRAINER_VERSION = TRAINER_VERSION
        core.FEATURES = FEATURES
        v5.FEATURES = FEATURES
        core._models = _models
        core._prepare = _prepare_exec_aligned
        core._head_metrics = _head_metrics_exec
        core._combined_metrics = _combined_metrics_exec
        core._choose_head = v5._robust_choose_head
        core._thin = v4r._rotating_thin
        splitbase._split = v5._robust_split
        rc = int(core.train(symbol, min_samples, data_root, hurdle_bp))
        if rc == 0:
            _annotate_report(symbol)
            print(f"V6_SUCCESS scope={symbol.upper()} champion_unchanged={CHAMPION_VERSION}")
            print("V6 target is execution-aligned; V5 robust gate was NOT loosened; test was NOT searched.")
        return rc
    finally:
        core.MODEL_DIR = old["model_dir"]
        core.TRAINER_VERSION = old["version"]
        core.FEATURES = old["features"]
        core._models = old["models"]
        core._prepare = old["prepare"]
        core._head_metrics = old["head_metrics"]
        core._combined_metrics = old["combined_metrics"]
        core._choose_head = old["choose"]
        core._thin = old["thin"]
        splitbase._split = old["split"]
        v5.FEATURES = old["v5_features"]


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
