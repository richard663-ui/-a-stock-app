# -*- coding: utf-8 -*-
"""Compatibility patch for Audit V2 permutation null.

Performance metrics stay 60-second non-overlap.  The leakage negative control is
threshold-independent and intentionally uses the dense final test day: a fixed
60-second sampling phase can alias a periodic label pattern into one class,
which makes ROC-AUC undefined.  Dense AUC here is diagnostic only and is never
reported as model performance or used for threshold/model selection.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np

import services.qmt_l1_60s_walkforward_v2 as audit


def _dense_null(dataset_root: Path, dates: List[str], repeats: int = 5) -> Dict[str, Any]:
    import services.train_l1_60s_model_v4 as core
    import services.train_l1_60s_model_v4r as v4r
    import services.train_l1_60s_model_v5_challenger as v5

    try:
        frame = core._prepare("ALL", dataset_root, audit.HURDLE_BP)
        if frame.empty:
            return {"ok": False, "reason": "empty_dataset"}
        test_day = str(dates[-1])
        train = frame[frame["trade_date"].astype(str) < test_day].copy()
        test = frame[frame["trade_date"].astype(str) == test_day].copy()
        train = v4r._rotating_thin(train, core.TRAIN_THIN_SECONDS)
        # Deliberately keep dense chronological test rows ONLY for the negative
        # control.  Headline OOS metrics elsewhere remain 60s non-overlap.
        if train.empty or test.empty:
            return {"ok": False, "reason": "empty_train_or_test"}

        y_test_up = test[core.UP_TARGET].astype(int).to_numpy()
        y_test_dn = test[core.DOWN_TARGET].astype(int).to_numpy()
        real_models = []
        for target in (core.UP_TARGET, core.DOWN_TARGET):
            model = v5._models()["logistic_balanced"]
            core._fit("logistic_balanced", model, train[core.FEATURES], train[target].astype(int).to_numpy())
            real_models.append(model)
        real = {
            "up_auc": audit._auc(y_test_up, core._positive_probability(real_models[0], test[core.FEATURES])),
            "down_auc": audit._auc(y_test_dn, core._positive_probability(real_models[1], test[core.FEATURES])),
        }

        y_up = train[core.UP_TARGET].astype(int).to_numpy()
        y_dn = train[core.DOWN_TARGET].astype(int).to_numpy()
        runs: List[Dict[str, Any]] = []
        for seed in range(int(repeats)):
            rng = np.random.default_rng(7300 + seed)
            up = v5._models()["logistic_balanced"]
            dn = v5._models()["logistic_balanced"]
            core._fit("logistic_balanced", up, train[core.FEATURES], rng.permutation(y_up))
            core._fit("logistic_balanced", dn, train[core.FEATURES], rng.permutation(y_dn))
            runs.append({
                "seed": seed,
                "up_auc": audit._auc(y_test_up, core._positive_probability(up, test[core.FEATURES])),
                "down_auc": audit._auc(y_test_dn, core._positive_probability(dn, test[core.FEATURES])),
            })
        vals = [float(x[k]) for x in runs for k in ("up_auc", "down_auc") if x.get(k) is not None]
        median = float(np.median(vals)) if vals else None
        return {
            "ok": bool(vals),
            "test_day": test_day,
            "dense_test_rows": int(len(test)),
            "repeats": int(repeats),
            "real_label_auc_diagnostic": real,
            "shuffled_label_runs": runs,
            "shuffled_auc_median": median,
            "leakage_alarm": bool(median is not None and (median < 0.42 or median > 0.58)),
            "sampling_policy": "dense_final_test_day_for_negative_control_only",
            "not_a_performance_metric": True,
            "expected": "shuffled-label median ROC-AUC should remain near 0.50; never used to tune thresholds/models",
            "reason": None if vals else "test_target_has_fewer_than_two_classes",
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def apply() -> None:
    audit._effective_null_control = _dense_null
