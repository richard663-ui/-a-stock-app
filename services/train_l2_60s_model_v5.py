# -*- coding: utf-8 -*-
"""V5 research wrapper: make the opening hour a mandatory OOS gate.

The underlying V4 models and leak-resistant validation protocol are preserved.
V5 adds regime-specific out-of-sample reporting and refuses shadow readiness if
09:30-10:30 performance is weak. Opening-auction rows are recorded for separate
research but excluded from the continuous-auction model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import joblib
import pandas as pd

import services.train_l2_60s_model_v4 as v4

base = v4.base
TRAINER_VERSION = "l2-60s-trainer-v5-morning-gated-20260903"
MIN_OPEN_TEST_N = 20
MIN_OPEN_DIRECTIONAL_N = 8
MIN_OPEN_ACCURACY_PCT = 55.0

_original_load_rows = base._load_rows


def _continuous_rows(data_root: Path) -> pd.DataFrame:
    frame = _original_load_rows(data_root)
    if frame.empty or "session" not in frame.columns:
        return frame
    return frame[frame["session"].astype(str).str.upper() != "OPEN_AUCTION"].copy()


def _segment(frame: pd.DataFrame, lo: float, hi: float) -> pd.DataFrame:
    if frame.empty or "minute_of_day" not in frame.columns:
        return frame.iloc[0:0].copy()
    minute = pd.to_numeric(frame["minute_of_day"], errors="coerce")
    return frame[(minute >= lo) & (minute < hi)].copy()


def _evaluate_segment(model, frame: pd.DataFrame, threshold: float, hurdle_bp: float) -> Dict[str, Any]:
    if frame.empty:
        return {"n": 0, "directional_predictions": 0}
    pred, _ = base._predict_selective(model, frame[v4.FEATURES], threshold)
    return v4._metrics("selective", frame, pred, hurdle_bp)


def _postprocess(symbol: str, data_root: Path, hurdle_bp: float) -> None:
    scope = symbol.upper().replace(".", "_")
    report_path = base.MODEL_DIR / f"{scope}_training_report_latest.json"
    if not report_path.exists():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))

    raw = _continuous_rows(data_root)
    if raw.empty:
        return
    frame = base._expand(raw)
    frame = v4._to_actionable(frame, hurdle_bp)
    frame = frame[(frame["valid"] == 1) & (frame["true_l2"] == 1) & frame[v4.ACTION_TARGET].isin([-1, 0, 1])].copy()
    if symbol.upper() != "ALL":
        frame = frame[frame["symbol"].str.upper() == symbol.upper()].copy()
    frame = frame.dropna(subset=[v4.ACTION_TARGET, base.RET_TARGET, "generated_ts"]).sort_values("generated_ts").reset_index(drop=True)

    old_target = base.TARGET
    base.TARGET = v4.ACTION_TARGET
    try:
        _, _, test_df, _ = base._split(frame)
        test_nonoverlap = base._nonoverlap(test_df)
    finally:
        base.TARGET = old_target

    regimes = {
        "OPEN_CORE_0930_1030": (570.0, 630.0),
        "AM_LATE_1030_1130": (630.0, 690.0),
        "AM_ALL_0930_1130": (570.0, 690.0),
        "PM_1300_1500": (780.0, 900.0),
    }
    report["trainer_version"] = TRAINER_VERSION
    report["base_trainer_version"] = v4.TRAINER_VERSION
    report["opening_auction_policy"] = "RECORDED_SEPARATELY_EXCLUDED_FROM_CONTINUOUS_MODEL"
    report["morning_priority_gate"] = {
        "window": "09:30-10:30",
        "min_test_nonoverlap_n": MIN_OPEN_TEST_N,
        "min_directional_predictions": MIN_OPEN_DIRECTIONAL_N,
        "min_directional_accuracy_pct": MIN_OPEN_ACCURACY_PCT,
        "require_positive_net_edge": True,
    }

    for name, item in report.get("models", {}).items():
        path = item.get("model_path")
        try:
            payload = joblib.load(path)
            model = payload["model"] if isinstance(payload, dict) else payload
        except Exception as exc:
            item["time_of_day_test_nonoverlap"] = {"error": f"model_load_failed:{exc}"}
            readiness = item.setdefault("readiness", {})
            reasons = list(readiness.get("reasons") or [])
            reasons.append("morning_gate_model_unavailable")
            readiness.update({"eligible_for_shadow_review": False, "reasons": list(dict.fromkeys(reasons)), "eligible_for_live_deployment": False})
            continue

        threshold = float(item.get("selected_probability_threshold") or v4.DEFAULT_NO_VALIDATION_THRESHOLD)
        tod: Dict[str, Any] = {}
        for label, (lo, hi) in regimes.items():
            tod[label] = _evaluate_segment(model, _segment(test_nonoverlap, lo, hi), threshold, hurdle_bp)
        item["time_of_day_test_nonoverlap"] = tod

        opening = tod["OPEN_CORE_0930_1030"]
        reasons = list((item.get("readiness") or {}).get("reasons") or [])
        if int(opening.get("n") or 0) < MIN_OPEN_TEST_N:
            reasons.append("opening_hour_test_too_small")
        if int(opening.get("directional_predictions") or 0) < MIN_OPEN_DIRECTIONAL_N:
            reasons.append("opening_hour_too_few_directional_signals")
        if float(opening.get("directional_accuracy_pct") or 0.0) < MIN_OPEN_ACCURACY_PCT:
            reasons.append("opening_hour_accuracy_below_55")
        if float(opening.get("avg_net_edge_bp") if opening.get("avg_net_edge_bp") is not None else -1e9) <= 0.0:
            reasons.append("opening_hour_net_edge_not_positive")
        reasons = list(dict.fromkeys(reasons))
        item["readiness"] = {
            "eligible_for_shadow_review": not reasons,
            "reasons": reasons,
            "eligible_for_live_deployment": False,
        }

    report["any_model_eligible_for_shadow_review"] = any(
        bool(x.get("readiness", {}).get("eligible_for_shadow_review")) for x in report.get("models", {}).values()
    )
    report["eligible_for_live_deployment"] = False
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def train(symbol: str, min_samples: int, data_root: Path, hurdle_bp: float) -> int:
    # V4 remains the model trainer; auction samples are kept out of the continuous model.
    old_loader = base._load_rows
    base._load_rows = _continuous_rows
    try:
        rc = int(v4.train(symbol, min_samples, data_root, hurdle_bp))
    finally:
        base._load_rows = old_loader
    if rc == 0:
        _postprocess(symbol, data_root, hurdle_bp)
        print("V5 morning gate applied: 09:30-10:30 must pass OOS accuracy and net-edge requirements.")
    return rc


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=base.PRIMARY_SYMBOL)
    p.add_argument("--min-samples", type=int, default=200)
    p.add_argument("--data-root", default=str(base.DATA_ROOT))
    p.add_argument("--hurdle-bp", type=float, default=v4.DEFAULT_HURDLE_BP)
    a = p.parse_args()
    raise SystemExit(train(a.symbol, a.min_samples, Path(a.data_root).expanduser(), a.hurdle_bp))


if __name__ == "__main__":
    main()
