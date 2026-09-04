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
    try:
        v5._models = _models
        v5.TRAINER_VERSION = TRAINER_VERSION
        rc = int(v5.train(symbol, min_samples, data_root, hurdle_bp))
        if rc == 0:
            _mark_roles(symbol)
            print(f"V5R_SUCCESS scope={symbol.upper()} champion_remains={v5.CHAMPION_VERSION}")
        return rc
    finally:
        v5._models = old_factory
        v5.TRAINER_VERSION = old_version


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
