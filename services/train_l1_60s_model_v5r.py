# -*- coding: utf-8 -*-
"""V5R runner: robust Logistic challenger + conservative HGB control.

Only Logistic is a promotion candidate. HGB is retained solely as a control so
we can detect whether extra non-linearity helps later without letting it replace
V4R while history is scarce.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

import services.train_l1_60s_model_v5_challenger as v5

TRAINER_VERSION = "l1-60s-trainer-v5r-robust-challenger-20260904"
DATA_ROOT = v5.DATA_ROOT
MODEL_DIR = v5.MODEL_DIR
_BASE_LOGISTIC_FACTORY = v5._models


def _models() -> Dict[str, Pipeline]:
    logistic = _BASE_LOGISTIC_FACTORY()["logistic_balanced"]
    control = Pipeline([
        ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("model", HistGradientBoostingClassifier(
            learning_rate=0.025,
            max_iter=180,
            max_leaf_nodes=7,
            min_samples_leaf=80,
            l2_regularization=5.0,
            random_state=42,
        )),
    ])
    return {"logistic_balanced": logistic, "hist_gradient_boosting": control}


def _series(frame: pd.DataFrame, name: str, default: float = np.nan) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce")


def _safe_up_proxy(frame: pd.DataFrame, bundle: Dict) -> Dict:
    """Missing-column-safe version of the V5 spread-adjusted UP proxy."""
    if frame.empty:
        return {"n": 0, "note": "proxy_only_no_test_rows"}
    if "ask1" not in frame.columns or "mid_price" not in frame.columns:
        return {
            "n": 0,
            "note": "proxy_unavailable_missing_entry_quote_columns; real recorder rows include ask1/mid_price",
        }
    threshold = float(bundle.get("up_threshold") or 0.999)
    prob = v5.core._positive_probability(bundle["up_model"], frame[v5.FEATURES])
    active = prob >= threshold
    ask = _series(frame, "ask1").to_numpy(float)
    future_ret = _series(frame, v5.core.RET_TARGET).to_numpy(float)
    entry_mid = _series(frame, "mid_price").to_numpy(float)
    spread_pct = _series(frame, "spread_pct", 0.0).fillna(0.0).clip(lower=0.0).to_numpy(float)
    future_mid = entry_mid * (1.0 + future_ret / 100.0)
    future_bid_proxy = future_mid * (1.0 - spread_pct / 200.0)
    ok = active & np.isfinite(ask) & (ask > 0) & np.isfinite(future_bid_proxy) & (future_bid_proxy > 0)
    n = int(ok.sum())
    if not n:
        return {"n": 0, "note": "proxy_only_no_active_up_signals"}
    ret_pct = (future_bid_proxy[ok] / ask[ok] - 1.0) * 100.0
    return {
        "n": n,
        "avg_spread_adjusted_proxy_edge_bp": float(np.mean(ret_pct) * 100.0),
        "median_spread_adjusted_proxy_edge_bp": float(np.median(ret_pct) * 100.0),
        "positive_proxy_return_pct": float((ret_pct > 0).mean() * 100.0),
        "note": "proxy_only: real entry ask; future bid approximated from smoothed future mid and current spread",
    }


def _mark_roles(symbol: str) -> None:
    scope = symbol.upper().replace(".", "_")
    path = MODEL_DIR / f"{scope}_training_report_latest.json"
    if not path.exists():
        return
    obj = json.loads(path.read_text(encoding="utf-8"))
    obj["trainer_version"] = TRAINER_VERSION
    obj["model_role_policy"] = "logistic=challenger_candidate; hgb=control_only"
    for name, item in obj.get("models", {}).items():
        item["candidate_role"] = "PROMOTION_CANDIDATE" if name == "logistic_balanced" else "CONTROL_ONLY"
        if name != "logistic_balanced":
            item["readiness"] = {
                "eligible_for_shadow_review": False,
                "eligible_for_live_deployment": False,
                "reasons": ["control_only", "scarce_history"],
            }
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def train(symbol: str, min_samples: int, data_root: Path, hurdle_bp: float) -> int:
    old_factory = v5._models
    old_version = v5.TRAINER_VERSION
    old_proxy = v5._up_spread_adjusted_proxy
    try:
        v5._models = _models
        v5.TRAINER_VERSION = TRAINER_VERSION
        v5._up_spread_adjusted_proxy = _safe_up_proxy
        rc = int(v5.train(symbol, min_samples, data_root, hurdle_bp))
        if rc == 0:
            _mark_roles(symbol)
            print(f"V5R_SUCCESS scope={symbol.upper()} champion_remains={v5.CHAMPION_VERSION}")
        return rc
    finally:
        v5._models = old_factory
        v5.TRAINER_VERSION = old_version
        v5._up_spread_adjusted_proxy = old_proxy


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
