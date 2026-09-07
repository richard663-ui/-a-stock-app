# -*- coding: utf-8 -*-
"""V4R-X: V4R with an execution-aware observed-bid scoring label.

Research-only challenger. The existing V4R benchmark is intentionally left
untouched so historical/prospective comparisons remain auditable.

Only the scoring/label semantics change:
- features, StandardScaler + Logistic C=0.35, HGB, rotating 15s thinning,
  validation threshold search and split policy remain V4R/V4;
- UP is scored on entry ask1 -> observed mean bid1 in +55..+65s;
- DOWN-risk is scored on entry bid1 -> the same observed future bid window;
- labels require a per-row action band equal to max(2bp research hurdle,
  recorder noise threshold, one A-share price tick as a percentage of entry);
- at least two valid future bid observations are required;
- the nearest +60s bid is retained only as a diagnostic, while the window mean
  is the primary label to reduce single-tick microstructure noise.

No production deployment, no same-day test tuning, and no automatic promotion.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd

import services.train_l1_60s_model_v4 as core
import services.train_l1_60s_model_v4r as v4r

TRAINER_VERSION = "l1-60s-trainer-v4rx-observed-bid-dynamic-label-20260907"
DATA_ROOT = core.DATA_ROOT
MODEL_DIR = core.MODEL_DIR / "v4rx_observed_label"
CHAMPION_REFERENCE = v4r.TRAINER_VERSION
UP_EXEC_RET = "ret_up_ask_to_observed_future_bid_60_pct"
DOWN_HOLD_RET = "ret_down_bid_to_observed_future_bid_60_pct"
FUTURE_BID_MEAN = "future_bid_observed_mean_55_65s"
FUTURE_BID_POINT = "future_bid_observed_nearest_60s"
FUTURE_BID_N = "future_bid_observed_count_55_65s"
LABEL_HURDLE = "observed_label_hurdle_pct"
TICK_SIZE_CNY = 0.01
TARGET_POLICY = (
    "UP=ask1_now->mean(observed bid1,+55..+65s);"
    "DOWN=bid1_now->same future bid;"
    "label_band=max(research_hurdle,recorder_threshold,one_price_tick_pct);"
    "min_future_bid_observations=2"
)


def _series(frame: pd.DataFrame, name: str, default: float = np.nan) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan)


def _attach_observed_future_bid(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach causal labels from later rows of the same symbol/trade date.

    This function is label construction only: all referenced rows are strictly
    in the future of each sample. Same-day/same-symbol grouping prevents lunch,
    overnight, and cross-symbol contamination.
    """
    out = frame.copy()
    out[FUTURE_BID_MEAN] = np.nan
    out[FUTURE_BID_POINT] = np.nan
    out[FUTURE_BID_N] = 0
    if out.empty:
        return out

    group_cols = ["symbol"]
    if "trade_date" in out.columns:
        group_cols.append("trade_date")

    for _, part in out.groupby(group_cols, sort=False, dropna=False):
        part = part.sort_values("generated_ts")
        ts = _series(part, "generated_ts").to_numpy(dtype=float)
        bid = _series(part, "bid1").to_numpy(dtype=float)
        if len(part) < 2 or not np.isfinite(ts).all():
            continue

        valid_bid = np.isfinite(bid) & (bid > 0)
        bid_sum = np.where(valid_bid, bid, 0.0)
        prefix_sum = np.concatenate(([0.0], np.cumsum(bid_sum)))
        prefix_n = np.concatenate(([0], np.cumsum(valid_bid.astype(np.int64))))

        lo = np.searchsorted(ts, ts + 55.0, side="left")
        hi = np.searchsorted(ts, ts + 65.0, side="right")
        counts = prefix_n[hi] - prefix_n[lo]
        sums = prefix_sum[hi] - prefix_sum[lo]
        means = np.full(len(part), np.nan, dtype=float)
        enough = counts >= 2
        means[enough] = sums[enough] / counts[enough]

        points = np.full(len(part), np.nan, dtype=float)
        target_ts = ts + 60.0
        right = np.searchsorted(ts, target_ts, side="left")
        for i, j in enumerate(right):
            best_j = -1
            best_dist = np.inf
            for cand in (j - 1, j):
                if 0 <= cand < len(ts) and valid_bid[cand]:
                    dist = abs(ts[cand] - target_ts[i])
                    if dist <= 4.0 and dist < best_dist:
                        best_j = cand
                        best_dist = dist
            if best_j >= 0:
                points[i] = bid[best_j]

        out.loc[part.index, FUTURE_BID_MEAN] = means
        out.loc[part.index, FUTURE_BID_POINT] = points
        out.loc[part.index, FUTURE_BID_N] = counts.astype(int)
    return out


def _apply_exec_labels(frame: pd.DataFrame, hurdle_bp: float) -> pd.DataFrame:
    out = frame.copy()
    ask = _series(out, "ask1")
    bid = _series(out, "bid1")
    future_bid = _series(out, FUTURE_BID_MEAN)
    recorded = _series(out, "label_threshold_pct", 0.01).fillna(0.01).clip(lower=0.0)

    cost_floor_pct = max(0.0, float(hurdle_bp)) / 100.0
    # Percentage move represented by one 0.01 CNY price tick at the current book.
    ref_px = ((ask + bid) / 2.0).where((ask > 0) & (bid > 0), np.nan)
    one_tick_pct = (TICK_SIZE_CNY / ref_px) * 100.0
    effective = np.maximum(recorded.to_numpy(float), cost_floor_pct)
    effective = np.maximum(effective, one_tick_pct.fillna(0.0).to_numpy(float))

    valid = (
        (ask > 0) & (bid > 0) & (ask >= bid) & (future_bid > 0) &
        (_series(out, FUTURE_BID_N, 0).fillna(0) >= 2)
    )
    up_ret = pd.Series(np.nan, index=out.index, dtype=float)
    down_ret = pd.Series(np.nan, index=out.index, dtype=float)
    up_ret.loc[valid] = (future_bid.loc[valid] / ask.loc[valid] - 1.0) * 100.0
    down_ret.loc[valid] = (future_bid.loc[valid] / bid.loc[valid] - 1.0) * 100.0
    out[UP_EXEC_RET] = up_ret
    out[DOWN_HOLD_RET] = down_ret
    out[LABEL_HURDLE] = effective

    out = out.dropna(subset=[UP_EXEC_RET, DOWN_HOLD_RET, "generated_ts"]).copy()
    eff = _series(out, LABEL_HURDLE).to_numpy(float)
    up = (_series(out, UP_EXEC_RET).to_numpy(float) > eff).astype(int)
    dn = (_series(out, DOWN_HOLD_RET).to_numpy(float) < -eff).astype(int)
    out[core.UP_TARGET] = up
    out[core.DOWN_TARGET] = dn
    out[core.ACTION_TARGET] = np.where((up == 1) & (dn == 0), 1,
                               np.where((dn == 1) & (up == 0), -1, 0)).astype(int)
    out["economic_hurdle_pct"] = eff
    return out.sort_values("generated_ts").reset_index(drop=True)


_ORIGINAL_PREPARE = core._prepare


def _prepare_observed(symbol: str, data_root: Path, hurdle_bp: float) -> pd.DataFrame:
    frame = _ORIGINAL_PREPARE(symbol, data_root, hurdle_bp)
    if frame.empty:
        return frame
    frame = _attach_observed_future_bid(frame)
    return _apply_exec_labels(frame, hurdle_bp)


def _head_metrics_exec(frame: pd.DataFrame, prob: np.ndarray, threshold: float,
                       direction: int, hurdle_bp: float, target_col: str) -> Dict[str, Any]:
    active = np.asarray(prob >= threshold)
    n = int(active.sum())
    if n:
        truth = frame[target_col].astype(int).to_numpy()[active]
        economic = (
            _series(frame, UP_EXEC_RET).to_numpy(float)[active] * 100.0
            if direction == 1
            else -_series(frame, DOWN_HOLD_RET).to_numpy(float)[active] * 100.0
        )
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
        accuracy = float(correct[finite].mean() * 100.0)
        economic = np.where(pred == 1, up_ret, -dn_ret) * 100.0
        gross = float(np.mean(economic[finite]))
        up_n = int(((pred == 1) & finite).sum())
        down_n = int(((pred == -1) & finite).sum())
    else:
        accuracy = gross = None
        up_n = down_n = 0
    return {
        "n": int(len(frame)), "directional_predictions": n,
        "directional_coverage_pct": 100.0 * n / len(frame) if len(frame) else None,
        "directional_accuracy_pct": accuracy,
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
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "candidate_role": "V4R_LABEL_CHALLENGER_ONLY",
        "champion_reference": CHAMPION_REFERENCE,
        "target": "direction_specific_observed_execution_label",
        "return_target": {"up": UP_EXEC_RET, "down": DOWN_HOLD_RET},
        "target_policy": TARGET_POLICY,
        "label_hurdle_policy": "max(research hurdle, recorder threshold, one 0.01 CNY tick pct)",
        "feature_policy": "exactly V4R/V4 feature set; scoring-label ablation only",
        "model_policy": "exactly V4R/V4 model families and hyperparameters",
        "threshold_policy": "V4 validation-nonoverlap threshold selection unchanged",
        "test_used_for_selection": False,
        "historical_backtest_can_promote": False,
        "requires_prospective_unseen_sessions": True,
        "auto_promoted": False,
        "eligible_for_live_deployment": False,
    })
    for item in (obj.get("models") or {}).values():
        item["candidate_role"] = "LABEL_ABLATION_CHALLENGER"
        item["readiness"] = {
            "eligible_for_shadow_review": False,
            "eligible_for_live_deployment": False,
            "reasons": ["new_scoring_label_challenger", "requires_prospective_unseen_sessions"],
        }
    obj["any_model_eligible_for_shadow_review"] = False
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def train(symbol: str, min_samples: int, data_root: Path, hurdle_bp: float) -> int:
    old = {
        "model_dir": core.MODEL_DIR,
        "version": core.TRAINER_VERSION,
        "prepare": core._prepare,
        "head_metrics": core._head_metrics,
        "combined_metrics": core._combined_metrics,
        "thin": core._thin,
    }
    try:
        core.MODEL_DIR = MODEL_DIR
        core.TRAINER_VERSION = TRAINER_VERSION
        core._prepare = _prepare_observed
        core._head_metrics = _head_metrics_exec
        core._combined_metrics = _combined_metrics_exec
        core._thin = v4r._rotating_thin
        rc = int(core.train(symbol, min_samples, data_root, hurdle_bp))
        if rc == 0:
            _annotate_report(symbol)
            print(f"V4RX_SUCCESS scope={symbol.upper()} champion_unchanged={CHAMPION_REFERENCE}")
            print("Observed future bid label only; V4R features/models/split/threshold policy unchanged.")
        return rc
    finally:
        core.MODEL_DIR = old["model_dir"]
        core.TRAINER_VERSION = old["version"]
        core._prepare = old["prepare"]
        core._head_metrics = old["head_metrics"]
        core._combined_metrics = old["combined_metrics"]
        core._thin = old["thin"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="ALL")
    p.add_argument("--min-samples", type=int, default=600)
    p.add_argument("--data-root", default=str(DATA_ROOT))
    p.add_argument("--hurdle-bp", type=float, default=2.0)
    a = p.parse_args()
    raise SystemExit(train(a.symbol, a.min_samples, Path(a.data_root).expanduser(), a.hurdle_bp))


if __name__ == "__main__":
    main()
