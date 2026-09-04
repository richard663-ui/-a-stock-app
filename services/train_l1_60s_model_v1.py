# -*- coding: utf-8 -*-
"""Leak-resistant L1/Tick 60s ML baseline trainer.

This intentionally reuses the mature V4/V5 validation and morning-gate logic,
but removes the true_l2 requirement and excludes all Level-2-derived features.
The purpose is to answer one clean question before paying for L2: can stable
QMT L1/Tick data alone produce positive out-of-sample 60s edge?
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd

import services.train_l2_60s_model_v4 as v4
import services.train_l2_60s_model_v5 as morning

base = v4.base
TRAINER_VERSION = "l1-60s-trainer-v1-purged-morning-gated-20260904"
L1_MODEL_DIR = Path.home() / "AStockData" / "models" / "l1_60s"

# Pure L1/Tick features only. No L2 placeholder/availability/baseline-rule fields.
L1_FEATURES = [
    "minute_of_day", "session_am", "minute_sin", "minute_cos",
    "phase_open_core", "phase_am_late", "phase_pm",
    "spread_pct", "depth5_imbalance_pct", "microprice_vs_mid_pct",
    "spread_change_5s", "depth5_imbalance_change_5s", "microprice_change_5s",
    "day_return_pct", "distance_high_pct", "distance_low_pct",
    "change_10s_pct", "change_30s_pct", "change_60s_pct", "change_120s_pct",
    "momentum_accel_10_30", "momentum_accel_30_60", "momentum_alignment_10_60",
    "short_move_abs_pct", "above_vwap_pct",
    "tick_buy_pct", "tick_buy_change_5s",
    "book_buy_pressure_pct", "book_pressure_change_5s", "pressure_change_pct",
    "log_volume_delta_5s", "log_amount_delta_5s",
    "volume_accel_5s", "amount_accel_5s",
    "market_hs300_return_pct", "market_chinext_return_pct",
    "relative_to_hs300_pct", "relative_to_chinext_pct",
]

_original_load = base._load_rows
_original_expand = base._expand


def _load_l1_rows(data_root: Path) -> pd.DataFrame:
    frame = _original_load(data_root)
    if frame.empty:
        return frame
    # Multiple recorder versions can overlap around an updater restart. Keep one
    # observation per symbol/5s bucket so duplicated runtime versions cannot
    # inflate the apparent sample size.
    if "sample_bucket" in frame.columns:
        frame = frame.sort_values("generated_ts").drop_duplicates(
            subset=["symbol", "sample_bucket"], keep="last"
        ).reset_index(drop=True)
    # V4/V5's proven training machinery historically required true_l2=1. The L1
    # wrapper marks eligible valid labels internally ONLY so that machinery can
    # be reused. No L2 features are present in L1_FEATURES.
    frame = frame.copy()
    frame["true_l2"] = 1
    return frame


def _expand_l1(frame: pd.DataFrame) -> pd.DataFrame:
    out = _original_expand(frame)
    if out.empty:
        return out
    out = out.sort_values(["symbol", "generated_ts"]).reset_index(drop=True)
    group_cols = ["symbol"]
    if "trade_date" in out.columns:
        group_cols.append("trade_date")
    g = out.groupby(group_cols, sort=False, dropna=False)

    def num(name: str, default: float = 0.0) -> pd.Series:
        if name not in out.columns:
            return pd.Series(default, index=out.index, dtype=float)
        return pd.to_numeric(out[name], errors="coerce").fillna(default)

    volume = num("volume")
    amount = num("amount")
    out["volume_delta_5s"] = g["volume"].diff().fillna(0.0).clip(lower=0.0) if "volume" in out.columns else 0.0
    out["amount_delta_5s"] = g["amount"].diff().fillna(0.0).clip(lower=0.0) if "amount" in out.columns else 0.0
    out["log_volume_delta_5s"] = np.log1p(pd.to_numeric(out["volume_delta_5s"], errors="coerce").fillna(0.0).clip(lower=0.0))
    out["log_amount_delta_5s"] = np.log1p(pd.to_numeric(out["amount_delta_5s"], errors="coerce").fillna(0.0).clip(lower=0.0))
    out["volume_accel_5s"] = out.groupby(group_cols, sort=False, dropna=False)["volume_delta_5s"].diff().fillna(0.0)
    out["amount_accel_5s"] = out.groupby(group_cols, sort=False, dropna=False)["amount_delta_5s"].diff().fillna(0.0)

    for src, dst in (
        ("spread_pct", "spread_change_5s"),
        ("depth5_imbalance_pct", "depth5_imbalance_change_5s"),
        ("microprice_vs_mid_pct", "microprice_change_5s"),
        ("tick_buy_pct", "tick_buy_change_5s"),
        ("book_buy_pressure_pct", "book_pressure_change_5s"),
    ):
        if src in out.columns:
            out[dst] = out.groupby(group_cols, sort=False, dropna=False)[src].diff().fillna(0.0)
        else:
            out[dst] = 0.0

    c10, c30, c60 = num("change_10s_pct"), num("change_30s_pct"), num("change_60s_pct")
    out["momentum_accel_10_30"] = c10 - c30 / 3.0
    out["momentum_accel_30_60"] = c30 - c60 / 2.0
    out["momentum_alignment_10_60"] = np.sign(c10 * c60)
    out["short_move_abs_pct"] = c10.abs() + c30.abs() / 3.0

    # Do not let a missing new feature fail an older day's rows. Median imputation
    # in the existing pipelines handles genuine missingness; numeric conversion
    # here keeps feature dtype deterministic.
    for c in L1_FEATURES:
        if c not in out.columns:
            out[c] = np.nan
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _annotate_report(symbol: str) -> None:
    scope = symbol.upper().replace(".", "_")
    path = L1_MODEL_DIR / f"{scope}_training_report_latest.json"
    if not path.exists():
        return
    report: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    report["trainer_version"] = TRAINER_VERSION
    report["data_mode"] = "L1_BASELINE"
    report["l2_required"] = False
    report["features"] = L1_FEATURES
    report["purpose"] = "Prove L1/Tick OOS alpha before purchasing Level-2."
    report["l2_purchase_benchmark"] = {
        "directional_accuracy_pct": 70.0,
        "require_positive_net_edge": True,
        "require_standard_multi_day_oos": True,
        "note": "Research milestone only; it does not authorize live deployment.",
    }
    maturity = str(report.get("maturity") or "")
    any_hit = False
    for item in report.get("models", {}).values():
        test = item.get("selected_threshold_test_nonoverlap") or {}
        hit = bool(
            maturity == "STANDARD"
            and int(test.get("directional_predictions") or 0) >= 30
            and float(test.get("directional_accuracy_pct") or 0.0) >= 70.0
            and float(test.get("avg_net_edge_bp") if test.get("avg_net_edge_bp") is not None else -1e9) > 0.0
        )
        item["l2_purchase_benchmark_reached"] = hit
        any_hit = any_hit or hit
    report["any_model_reached_l2_purchase_benchmark"] = any_hit
    report["eligible_for_live_deployment"] = False
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def train(symbol: str, min_samples: int, data_root: Path, hurdle_bp: float) -> int:
    # Patch only this research subprocess. The original L2 trainers remain intact
    # on disk for a later L1+L2 A/B comparison.
    base.MODEL_DIR = L1_MODEL_DIR
    base.FEATURES = L1_FEATURES
    base._load_rows = _load_l1_rows
    base._expand = _expand_l1
    morning._original_load_rows = _load_l1_rows
    v4.FEATURES = L1_FEATURES
    v4.TRAINER_VERSION = TRAINER_VERSION
    morning.TRAINER_VERSION = TRAINER_VERSION

    rc = int(morning.train(symbol, min_samples, data_root, hurdle_bp))
    if rc == 0:
        _annotate_report(symbol)
        print("L1_BASELINE complete: no Level-2 feature or permission was used.")
        print("70% is tracked only as a multi-day non-overlap OOS milestone, never as an in-sample target.")
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
