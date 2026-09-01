# -*- coding: utf-8 -*-
"""Train research-only 60s classifiers from the persistent QMT L2 dataset.

Primary target: label_smoothed_mid_60 (-1/0/+1).
Models: balanced multinomial logistic regression and histogram gradient boosting.
Evaluation is chronological. With >=3 trading days, the last day is held out for
final test and the penultimate day is validation. Nothing is auto-deployed.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, confusion_matrix, f1_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

DATA_ROOT = Path.home() / "AStockData"
MODEL_DIR = DATA_ROOT / "models" / "l2_60s"
PRIMARY_SYMBOL = "301236.SZ"
TARGET = "label_smoothed_mid_60"
RET_TARGET = "ret_smoothed_mid_60_pct"

FEATURES = [
    "minute_of_day", "session_am", "spread_pct", "depth5_imbalance_pct",
    "microprice_vs_mid_pct", "day_return_pct", "distance_high_pct", "distance_low_pct",
    "change_10s_pct", "change_30s_pct", "change_60s_pct", "change_120s_pct",
    "above_vwap_pct", "tick_buy_pct", "book_buy_pressure_pct", "pressure_change_pct",
    "l2_active_buy_pct", "l2_big_buy_pct", "l2_order_buy_pct",
    "l2_cancel_sell_support_pct", "l2_total_book_buy_pct", "l2_depth10_buy_pct",
    "l2_queue_buy_pct", "l2_ddx", "l2_ddy", "l2_ddz", "l2_net_order",
    "l2_agreement", "l2_up_votes", "l2_down_votes",
    "available_l2quote", "available_l2transaction", "available_l2order",
    "available_l2quoteaux", "available_l2transactioncount", "available_l2orderqueue",
    "baseline_agreement", "baseline_high_confidence", "baseline_selective_gate",
    "log_volume", "log_amount", "minute_sin", "minute_cos",
]


def _load_rows(data_root: Path) -> pd.DataFrame:
    paths = sorted((data_root / "training").glob("*/l2_training.sqlite3"))
    frames: List[pd.DataFrame] = []
    for path in paths:
        conn = sqlite3.connect(path, timeout=10.0)
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_samples_v1'"
            ).fetchone()
            if not exists:
                continue
            frame = pd.read_sql_query(
                "SELECT * FROM training_samples_v1 WHERE labeled_at IS NOT NULL ORDER BY generated_ts",
                conn,
            )
            if not frame.empty:
                frame["source_db"] = str(path)
                frames.append(frame)
        finally:
            conn.close()
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("generated_ts").reset_index(drop=True)


def _expand_features(frame: pd.DataFrame) -> pd.DataFrame:
    records: List[Dict[str, Any]] = []
    for raw in frame.get("features_json", pd.Series(dtype=str)).fillna("{}"):
        try:
            obj = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception:
            obj = {}
        records.append(obj if isinstance(obj, dict) else {})
    fx = pd.DataFrame(records)
    out = pd.concat([frame.reset_index(drop=True), fx.reset_index(drop=True)], axis=1)
    out["generated_dt"] = pd.to_datetime(out["generated_at"], errors="coerce")
    out["trade_date"] = out["generated_dt"].dt.date.astype(str)
    out["volume"] = pd.to_numeric(out.get("volume"), errors="coerce").fillna(0.0)
    out["amount"] = pd.to_numeric(out.get("amount"), errors="coerce").fillna(0.0)
    out["log_volume"] = np.log1p(out["volume"].clip(lower=0.0))
    out["log_amount"] = np.log1p(out["amount"].clip(lower=0.0))
    minute = pd.to_numeric(out.get("minute_of_day"), errors="coerce").fillna(0.0)
    angle = 2.0 * np.pi * minute / (24.0 * 60.0)
    out["minute_sin"] = np.sin(angle)
    out["minute_cos"] = np.cos(angle)
    for c in FEATURES:
        out[c] = pd.to_numeric(out.get(c), errors="coerce")
    out[TARGET] = pd.to_numeric(out.get(TARGET), errors="coerce")
    out[RET_TARGET] = pd.to_numeric(out.get(RET_TARGET), errors="coerce")
    return out


def _chronological_split(frame: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    dates = [d for d in sorted(frame["trade_date"].dropna().unique()) if d and d != "NaT"]
    if len(dates) >= 3:
        train_dates, val_date, test_date = dates[:-2], dates[-2], dates[-1]
        return (
            frame[frame["trade_date"].isin(train_dates)].copy(),
            frame[frame["trade_date"] == val_date].copy(),
            frame[frame["trade_date"] == test_date].copy(),
            f"day_holdout train={train_dates[0]}..{train_dates[-1]} val={val_date} test={test_date}",
        )
    if len(dates) == 2:
        first, second = dates
        train = frame[frame["trade_date"] == first].copy()
        test = frame[frame["trade_date"] == second].copy()
        return train, pd.DataFrame(columns=frame.columns), test, f"two_day_holdout train={first} test={second}"
    n = len(frame)
    cut1 = max(1, int(n * 0.70))
    cut2 = max(cut1 + 1, int(n * 0.85))
    cut2 = min(cut2, n - 1)
    return frame.iloc[:cut1].copy(), frame.iloc[cut1:cut2].copy(), frame.iloc[cut2:].copy(), "PROVISIONAL single-day chronological 70/15/15"


def _edge_stats(y_true: Sequence[int], y_pred: Sequence[int], returns: Sequence[float]) -> Dict[str, Any]:
    edges: List[float] = []
    correct = 0
    considered = 0
    for yt, yp, r in zip(y_true, y_pred, returns):
        if int(yp) not in (-1, 1) or not math.isfinite(float(r)):
            continue
        considered += 1
        edge = float(r) if int(yp) == 1 else -float(r)
        edges.append(edge)
        if int(yt) == int(yp):
            correct += 1
    return {
        "directional_predictions": considered,
        "directional_coverage_pct": 100.0 * considered / len(y_pred) if len(y_pred) else None,
        "directional_accuracy_pct": 100.0 * correct / considered if considered else None,
        "avg_directional_edge_pct": float(np.mean(edges)) if edges else None,
        "avg_directional_edge_bp": float(np.mean(edges) * 100.0) if edges else None,
    }


def _metrics(name: str, y_true: np.ndarray, y_pred: np.ndarray, returns: np.ndarray) -> Dict[str, Any]:
    return {
        "model": name,
        "n": int(len(y_true)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)) if len(y_true) else None,
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)) if len(y_true) else None,
        "confusion_matrix_labels_-1_0_1": confusion_matrix(y_true, y_pred, labels=[-1, 0, 1]).tolist() if len(y_true) else [],
        "class_distribution_true": {str(k): int(v) for k, v in pd.Series(y_true).value_counts().sort_index().items()},
        "class_distribution_pred": {str(k): int(v) for k, v in pd.Series(y_pred).value_counts().sort_index().items()},
        **_edge_stats(y_true, y_pred, returns),
    }


def _baseline_metrics(test: pd.DataFrame) -> Dict[str, Any]:
    mapping = {"DOWN": -1, "WATCH": 0, "UP": 1}
    pred = test.get("baseline_direction", pd.Series(index=test.index, dtype=object)).map(mapping).fillna(0).astype(int).to_numpy()
    y = test[TARGET].astype(int).to_numpy()
    ret = test[RET_TARGET].astype(float).to_numpy()
    return _metrics("existing_direction_v18_baseline", y, pred, ret)


def _build_models() -> Dict[str, Any]:
    logistic = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("model", LogisticRegression(max_iter=2500, class_weight="balanced", C=0.5, solver="lbfgs")),
    ])
    hgb = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("model", HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=220,
            max_leaf_nodes=15,
            min_samples_leaf=30,
            l2_regularization=1.0,
            random_state=42,
        )),
    ])
    return {"logistic_balanced": logistic, "hist_gradient_boosting": hgb}


def train(symbol: str, min_samples: int, data_root: Path) -> int:
    raw = _load_rows(data_root)
    if raw.empty:
        print("No labeled L2 training data found yet.")
        return 2
    frame = _expand_features(raw)
    frame = frame[(frame["valid"] == 1) & (frame["true_l2"] == 1) & frame[TARGET].isin([-1, 0, 1])].copy()
    if symbol.upper() != "ALL":
        frame = frame[frame["symbol"].str.upper() == symbol.upper()].copy()
    frame = frame.dropna(subset=[TARGET, RET_TARGET, "generated_ts"]).sort_values("generated_ts").reset_index(drop=True)
    if len(frame) < min_samples:
        print(f"Need at least {min_samples} valid true-L2 labeled samples; found {len(frame)}.")
        print("Keep qmt_l2_training_recorder running during market hours; do not tune weights yet.")
        return 2
    train_df, val_df, test_df, protocol = _chronological_split(frame)
    if train_df.empty or test_df.empty:
        print("Not enough chronological data to create train/test blocks.")
        return 2

    X_train, y_train = train_df[FEATURES], train_df[TARGET].astype(int).to_numpy()
    X_test, y_test = test_df[FEATURES], test_df[TARGET].astype(int).to_numpy()
    test_ret = test_df[RET_TARGET].astype(float).to_numpy()
    X_val = val_df[FEATURES] if not val_df.empty else None
    y_val = val_df[TARGET].astype(int).to_numpy() if not val_df.empty else None
    val_ret = val_df[RET_TARGET].astype(float).to_numpy() if not val_df.empty else None

    target_classes = sorted(set(int(x) for x in y_train))
    if len(target_classes) < 2:
        print(f"Training block has only one target class: {target_classes}. Need more data.")
        return 2

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    scope = symbol.upper().replace(".", "_")
    report: Dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": symbol.upper(),
        "target": TARGET,
        "return_target": RET_TARGET,
        "protocol": protocol,
        "feature_count": len(FEATURES),
        "features": FEATURES,
        "samples_total": int(len(frame)),
        "samples_train": int(len(train_df)),
        "samples_validation": int(len(val_df)),
        "samples_test": int(len(test_df)),
        "baseline_test": _baseline_metrics(test_df),
        "models": {},
        "auto_deployed": False,
    }

    for name, model in _build_models().items():
        model.fit(X_train, y_train)
        test_pred = model.predict(X_test)
        item: Dict[str, Any] = {"test": _metrics(name, y_test, test_pred, test_ret)}
        if X_val is not None and y_val is not None and len(y_val):
            val_pred = model.predict(X_val)
            item["validation"] = _metrics(name, y_val, val_pred, val_ret)
        model_path = MODEL_DIR / f"{scope}_{name}_{stamp}.joblib"
        joblib.dump({"model": model, "features": FEATURES, "target": TARGET, "scope": symbol.upper()}, model_path)
        item["model_path"] = str(model_path)
        report["models"][name] = item

    report_path = MODEL_DIR / f"{scope}_training_report_latest.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 78)
    print("AStock trainable 60s L2 research")
    print(f"Scope: {symbol.upper()}  samples={len(frame)}")
    print(f"Protocol: {protocol}")
    b = report["baseline_test"]
    print(f"Existing baseline test: bal_acc={b['balanced_accuracy']:.3f} dir_acc={b['directional_accuracy_pct']} edge_bp={b['avg_directional_edge_bp']}")
    for name, item in report["models"].items():
        m = item["test"]
        print(f"{name}: bal_acc={m['balanced_accuracy']:.3f} macro_f1={m['macro_f1']:.3f} dir_acc={m['directional_accuracy_pct']} edge_bp={m['avg_directional_edge_bp']}")
    print(f"Report: {report_path}")
    print("Research-only: no model was deployed to the live/mobile engine.")
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=PRIMARY_SYMBOL, help="301236.SZ or ALL")
    p.add_argument("--min-samples", type=int, default=200)
    p.add_argument("--data-root", default=str(DATA_ROOT))
    args = p.parse_args()
    raise SystemExit(train(args.symbol, args.min_samples, Path(args.data_root).expanduser()))


if __name__ == "__main__":
    main()
