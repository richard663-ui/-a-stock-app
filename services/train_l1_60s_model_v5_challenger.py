# -*- coding: utf-8 -*-
"""Robust L1/Tick 60s challenger V5.

CHALLENGER ONLY: V4R remains the champion.

Scarce-data policy:
- keep asymmetric UP-entry / DOWN-risk heads;
- use only strongly regularized Logistic Regression;
- keep rotating 15s train thinning and 60s non-overlap validation/test;
- widen validation to two days only after enough history exists;
- enable a head only when validation edge is positive and stable across
  time segments / threshold neighbours; otherwise force WATCH;
- never use test data to choose thresholds or activate a head;
- report a conservative spread-adjusted UP-entry proxy (not exact execution);
- never auto-deploy or replace V4R.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import services.train_l1_60s_model_v4 as core
import services.train_l1_60s_model_v4r as v4r
import services.train_l2_60s_model_v3 as splitbase

TRAINER_VERSION = "l1-60s-trainer-v5-robust-challenger-20260904"
DATA_ROOT = core.DATA_ROOT
MODEL_DIR = core.MODEL_DIR / "v5_challenger"
CHAMPION_VERSION = v4r.TRAINER_VERSION
FEATURES = list(core.FEATURES)
PROB_THRESHOLDS = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
MIN_HISTORY_DAYS_FOR_PROMOTION = 5
MIN_WALK_FORWARD_FOLDS_FOR_PROMOTION = 2
MIN_PROMOTION_SIGNALS = 60
MIN_PROMOTION_ACCURACY = 55.0
MIN_PROMOTION_NET_BP = 1.5
_LAST_SPLIT_META: Dict[str, Any] = {}


def _robust_split(frame: pd.DataFrame):
    global _LAST_SPLIT_META
    dates = [d for d in sorted(frame["trade_date"].dropna().unique()) if d and d != "NaT"]
    if len(dates) >= 5:
        train_dates, val_dates, test_date = dates[:-3], dates[-3:-1], dates[-1]
        meta = {
            "protocol": f"ROBUST_DAY_HOLDOUT train={train_dates[0]}..{train_dates[-1]} val={val_dates[0]}..{val_dates[-1]} test={test_date}",
            "out_of_sample_by_day": True, "maturity": "STANDARD", "purge_seconds": 0,
            "history_days": len(dates), "validation_days": 2, "history_grade": "MULTI_VALIDATION",
        }
        _LAST_SPLIT_META = meta
        return (
            frame[frame["trade_date"].isin(train_dates)].copy(),
            frame[frame["trade_date"].isin(val_dates)].copy(),
            frame[frame["trade_date"] == test_date].copy(), meta,
        )
    if len(dates) == 4:
        train_dates, val_date, test_date = dates[:2], dates[2], dates[3]
        meta = {
            "protocol": f"FOUR_DAY_HOLDOUT train={train_dates[0]}..{train_dates[-1]} val={val_date} test={test_date}",
            "out_of_sample_by_day": True, "maturity": "STANDARD", "purge_seconds": 0,
            "history_days": 4, "validation_days": 1, "history_grade": "EARLY_4_DAY",
        }
        _LAST_SPLIT_META = meta
        return (
            frame[frame["trade_date"].isin(train_dates)].copy(),
            frame[frame["trade_date"] == val_date].copy(),
            frame[frame["trade_date"] == test_date].copy(), meta,
        )
    if len(dates) == 3:
        meta = {
            "protocol": f"THIN_3_DAY_HOLDOUT train={dates[0]} val={dates[1]} test={dates[2]}",
            "out_of_sample_by_day": True, "maturity": "STANDARD", "purge_seconds": 0,
            "history_days": 3, "validation_days": 1, "history_grade": "THIN_3_DAY",
        }
        _LAST_SPLIT_META = meta
        return (
            frame[frame["trade_date"] == dates[0]].copy(),
            frame[frame["trade_date"] == dates[1]].copy(),
            frame[frame["trade_date"] == dates[2]].copy(), meta,
        )
    out = splitbase._split(frame)
    _LAST_SPLIT_META = dict(out[3] or {})
    _LAST_SPLIT_META.update({
        "history_days": len(dates), "validation_days": 0 if len(dates) < 3 else 1,
        "history_grade": "INSUFFICIENT_MULTI_DAY",
    })
    return out


def _models() -> Dict[str, Pipeline]:
    return {
        "logistic_balanced": Pipeline([
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(
                max_iter=3000, class_weight="balanced", C=0.12, solver="lbfgs"
            )),
        ])
    }


def _phase_groups(frame: pd.DataFrame) -> List[Tuple[str, pd.DataFrame]]:
    out: List[Tuple[str, pd.DataFrame]] = []
    for label, lo, hi in (
        ("OPEN_CORE", 570.0, 630.0), ("AM_LATE", 630.0, 690.0), ("PM", 780.0, 900.0)
    ):
        part = core._segment(frame, lo, hi)
        if not part.empty:
            out.append((label, part))
    return out


def _metric(model: Pipeline, frame: pd.DataFrame, threshold: float, direction: int,
            hurdle_bp: float, target_col: str) -> Dict[str, Any]:
    prob = core._positive_probability(model, frame[FEATURES])
    return core._head_metrics(frame, prob, threshold, direction, hurdle_bp, target_col)


def _robust_choose_head(model: Pipeline, val: pd.DataFrame, direction: int,
                        hurdle_bp: float, target_col: str):
    if val.empty:
        return 0.999, {"disabled_head": True, "reason": "no_validation"}, []

    prob = core._positive_probability(model, val[FEATURES])
    min_n = max(12, int(math.ceil(len(val) * 0.04)))
    min_group_n = max(4, int(math.ceil(min_n * 0.20)))
    base_metrics: Dict[float, Dict[str, Any]] = {
        float(t): core._head_metrics(val, prob, t, direction, hurdle_bp, target_col)
        for t in PROB_THRESHOLDS
    }
    dates = [d for d in sorted(val.get("trade_date", pd.Series(dtype=str)).dropna().astype(str).unique()) if d and d != "NaT"]
    phases = _phase_groups(val)
    curve: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []

    for i, t in enumerate(PROB_THRESHOLDS):
        m = dict(base_metrics[float(t)])
        n = int(m.get("directional_predictions") or 0)
        net = m.get("avg_net_edge_bp")
        acc = m.get("directional_accuracy_pct")

        day_stats: Dict[str, Any] = {}
        for d in dates:
            part = val[val["trade_date"].astype(str) == str(d)].copy()
            gm = _metric(model, part, t, direction, hurdle_bp, target_col)
            if int(gm.get("directional_predictions") or 0) >= min_group_n:
                day_stats[str(d)] = gm

        phase_stats: Dict[str, Any] = {}
        for label, part in phases:
            gm = _metric(model, part, t, direction, hurdle_bp, target_col)
            if int(gm.get("directional_predictions") or 0) >= min_group_n:
                phase_stats[label] = gm

        neighbour_nets: List[float] = []
        for j in (i - 1, i + 1):
            if 0 <= j < len(PROB_THRESHOLDS):
                nm = base_metrics[float(PROB_THRESHOLDS[j])]
                if int(nm.get("directional_predictions") or 0) >= max(6, int(min_n * 0.60)):
                    x = nm.get("avg_net_edge_bp")
                    if x is not None:
                        neighbour_nets.append(float(x))

        group_nets = [
            float(gm["avg_net_edge_bp"])
            for gm in list(day_stats.values()) + list(phase_stats.values())
            if gm.get("avg_net_edge_bp") is not None
        ]
        neighbour_median = float(np.median(neighbour_nets)) if neighbour_nets else None
        group_median = float(np.median(group_nets)) if group_nets else None
        group_positive_fraction = (
            float(sum(x > 0.0 for x in group_nets) / len(group_nets)) if group_nets else None
        )

        reasons: List[str] = []
        if n < min_n:
            reasons.append("too_few_validation_signals")
        if net is None or float(net) <= 0.0:
            reasons.append("validation_net_not_positive")
        if acc is None or float(acc) < 50.0:
            reasons.append("validation_accuracy_below_50")
        if neighbour_median is None or neighbour_median <= -0.5:
            reasons.append("threshold_neighbour_unstable")
        if len(group_nets) >= 2:
            if group_median is None or group_median <= 0.0:
                reasons.append("time_segment_median_not_positive")
            if group_positive_fraction is None or group_positive_fraction < 0.5:
                reasons.append("time_segment_support_below_half")

        floor_parts = [float(net)] if net is not None else []
        if neighbour_median is not None:
            floor_parts.append(neighbour_median)
        if group_median is not None and len(group_nets) >= 2:
            floor_parts.append(group_median)
        robust_floor = min(floor_parts) if floor_parts else -1e9
        item = {
            "probability_threshold": float(t), **m,
            "min_required_signals": min_n,
            "validation_day_stats": day_stats,
            "validation_phase_stats": phase_stats,
            "neighbour_median_net_bp": neighbour_median,
            "time_segment_median_net_bp": group_median,
            "time_segment_positive_fraction": group_positive_fraction,
            "robust_floor_net_bp": robust_floor,
            "robust_score": robust_floor * math.sqrt(max(1, n)),
            "eligible": not reasons,
            "rejection_reasons": reasons,
        }
        curve.append(item)
        if not reasons:
            candidates.append(item)

    if not candidates:
        return 0.999, {
            "disabled_head": True, "reason": "no_validation_stable_threshold",
            "min_required_signals": min_n,
            "direction": "UP" if direction == 1 else "DOWN",
        }, curve

    best = max(candidates, key=lambda x: (
        float(x.get("robust_score") or -1e9),
        float(x.get("directional_accuracy_pct") or 0.0),
        int(x.get("directional_predictions") or 0),
    ))
    selected = dict(best)
    selected.update({
        "disabled_head": False,
        "selection_policy": "positive_net+accuracy>=50+neighbour_stability+time_segment_stability",
    })
    return float(best["probability_threshold"]), selected, curve


def _up_spread_adjusted_proxy(frame: pd.DataFrame, bundle: Dict[str, Any]) -> Dict[str, Any]:
    if frame.empty:
        return {"n": 0, "note": "proxy_only_no_future_bid_history"}
    threshold = float(bundle.get("up_threshold") or 0.999)
    prob = core._positive_probability(bundle["up_model"], frame[FEATURES])
    active = prob >= threshold
    ask = pd.to_numeric(frame.get("ask1"), errors="coerce").to_numpy(float)
    future_ret = pd.to_numeric(frame.get(core.RET_TARGET), errors="coerce").to_numpy(float)
    entry_mid = pd.to_numeric(frame.get("mid_price"), errors="coerce").to_numpy(float)
    spread_pct = pd.to_numeric(frame.get("spread_pct"), errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(float)
    future_mid = entry_mid * (1.0 + future_ret / 100.0)
    future_bid_proxy = future_mid * (1.0 - spread_pct / 200.0)
    ok = active & np.isfinite(ask) & (ask > 0) & np.isfinite(future_bid_proxy) & (future_bid_proxy > 0)
    n = int(ok.sum())
    if not n:
        return {"n": 0, "note": "proxy_only_no_future_bid_history"}
    ret_pct = (future_bid_proxy[ok] / ask[ok] - 1.0) * 100.0
    return {
        "n": n,
        "avg_spread_adjusted_proxy_edge_bp": float(np.mean(ret_pct) * 100.0),
        "median_spread_adjusted_proxy_edge_bp": float(np.median(ret_pct) * 100.0),
        "positive_proxy_return_pct": float((ret_pct > 0).mean() * 100.0),
        "note": "proxy_only: real entry ask; future bid approximated from smoothed future mid and current spread",
    }


def _augment_report(symbol: str, data_root: Path) -> None:
    import joblib
    scope = symbol.upper().replace(".", "_")
    path = MODEL_DIR / f"{scope}_training_report_latest.json"
    if not path.exists():
        return
    obj = json.loads(path.read_text(encoding="utf-8"))
    history_days = int((_LAST_SPLIT_META or {}).get("history_days") or 0)
    obj.update({
        "trainer_version": TRAINER_VERSION,
        "candidate_role": "CHALLENGER_ONLY",
        "champion_reference": CHAMPION_VERSION,
        "history_days": history_days,
        "history_grade": (_LAST_SPLIT_META or {}).get("history_grade"),
        "validation_days": int((_LAST_SPLIT_META or {}).get("validation_days") or 0),
        "threshold_policy": "validation-only robust stability gating; unstable heads disabled",
        "feature_policy": "no_feature_expansion_vs_v4; stronger_logistic_regularization",
        "test_used_for_selection": False,
        "auto_promoted": False,
        "eligible_for_live_deployment": False,
        "promotion_policy": {
            "min_history_days": MIN_HISTORY_DAYS_FOR_PROMOTION,
            "min_walk_forward_folds": MIN_WALK_FORWARD_FOLDS_FOR_PROMOTION,
            "min_directional_test_signals": MIN_PROMOTION_SIGNALS,
            "min_directional_accuracy_pct": MIN_PROMOTION_ACCURACY,
            "min_net_edge_bp": MIN_PROMOTION_NET_BP,
            "requires_positive_opening_net_edge": True,
            "requires_future_unseen_days": True,
        },
        "eligible_for_champion_promotion": False,
        "promotion_reasons": [
            "challenger_never_auto_promotes",
            "need_more_unseen_trade_days" if history_days < MIN_HISTORY_DAYS_FOR_PROMOTION else "needs_multi_fold_confirmation",
        ],
    })
    try:
        frame = core._prepare(symbol, data_root, float(obj.get("execution_hurdle_bp") or 2.0))
        _, _, test_df, _ = _robust_split(frame)
        non_test = splitbase._nonoverlap(test_df)
        for item in obj.get("models", {}).values():
            model_path = Path(str(item.get("model_path") or ""))
            if model_path.exists():
                bundle = joblib.load(model_path)
                item["up_entry_spread_adjusted_proxy"] = _up_spread_adjusted_proxy(non_test, bundle)
            item["readiness"] = {
                "eligible_for_shadow_review": False,
                "eligible_for_live_deployment": False,
                "reasons": ["challenger_only", "need_more_unseen_trade_days"],
            }
    except Exception as exc:
        obj["execution_proxy_error"] = str(exc)
    obj["any_model_eligible_for_shadow_review"] = False
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def train(symbol: str, min_samples: int, data_root: Path, hurdle_bp: float) -> int:
    old_model_dir, old_version = core.MODEL_DIR, core.TRAINER_VERSION
    old_models, old_choose, old_thin, old_split = core._models, core._choose_head, core._thin, splitbase._split
    try:
        core.MODEL_DIR = MODEL_DIR
        core.TRAINER_VERSION = TRAINER_VERSION
        core._models = _models
        core._choose_head = _robust_choose_head
        core._thin = v4r._rotating_thin
        splitbase._split = _robust_split
        rc = int(core.train(symbol, min_samples, data_root, hurdle_bp))
        if rc == 0:
            _augment_report(symbol, data_root)
            print(f"V5_CHALLENGER_SUCCESS scope={symbol.upper()} champion_unchanged={CHAMPION_VERSION}")
        return rc
    finally:
        core.MODEL_DIR, core.TRAINER_VERSION = old_model_dir, old_version
        core._models, core._choose_head, core._thin, splitbase._split = old_models, old_choose, old_thin, old_split


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
