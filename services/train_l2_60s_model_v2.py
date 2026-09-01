# -*- coding: utf-8 -*-
"""Train research-only 60-second ML baselines from training_samples_v2.

Primary target is the 3-class smoothed mid-price label at 60 seconds. The script
fits a balanced logistic model and histogram gradient boosting, compares both
against the existing direction_v18 baseline on the same held-out block, and
never auto-deploys a model.
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
    "minute_of_day", "session_am", "spread_pct", "depth5_imbalance_pct", "microprice_vs_mid_pct",
    "day_return_pct", "distance_high_pct", "distance_low_pct",
    "change_10s_pct", "change_30s_pct", "change_60s_pct", "change_120s_pct", "above_vwap_pct",
    "tick_buy_pct", "book_buy_pressure_pct", "pressure_change_pct",
    "l2_active_buy_pct", "l2_big_buy_pct", "l2_order_buy_pct", "l2_cancel_sell_support_pct",
    "l2_total_book_buy_pct", "l2_depth10_buy_pct", "l2_queue_buy_pct",
    "l2_ddx", "l2_ddy", "l2_ddz", "l2_net_order", "l2_agreement", "l2_up_votes", "l2_down_votes",
    "available_l2quote", "available_l2transaction", "available_l2order", "available_l2quoteaux",
    "available_l2transactioncount", "available_l2orderqueue",
    "baseline_agreement", "baseline_high_confidence", "baseline_selective_gate",
    "log_volume", "log_amount", "minute_sin", "minute_cos",
]


def _load_rows(data_root: Path) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for path in sorted((data_root / "training").glob("*/l2_training.sqlite3")):
        conn = sqlite3.connect(path, timeout=10.0)
        try:
            ok = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_samples_v2'").fetchone()
            if not ok:
                continue
            frame = pd.read_sql_query(
                "SELECT * FROM training_samples_v2 WHERE labeled_at IS NOT NULL ORDER BY generated_ts", conn
            )
            if not frame.empty:
                frame["source_db"] = str(path); frames.append(frame)
        finally:
            conn.close()
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("generated_ts").reset_index(drop=True)


def _expand(frame: pd.DataFrame) -> pd.DataFrame:
    records: List[Dict[str, Any]] = []
    for raw in frame["features_json"].fillna("{}"):
        try:
            obj = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception:
            obj = {}
        records.append(obj if isinstance(obj, dict) else {})
    fx = pd.DataFrame(records)
    out = pd.concat([frame.reset_index(drop=True), fx.reset_index(drop=True)], axis=1)
    out["generated_dt"] = pd.to_datetime(out["generated_at"], errors="coerce")
    out["trade_date"] = out["generated_dt"].dt.date.astype(str)
    volume = pd.to_numeric(out.get("volume"), errors="coerce").fillna(0.0)
    amount = pd.to_numeric(out.get("amount"), errors="coerce").fillna(0.0)
    out["log_volume"] = np.log1p(volume.clip(lower=0.0))
    out["log_amount"] = np.log1p(amount.clip(lower=0.0))
    minute = pd.to_numeric(out.get("minute_of_day"), errors="coerce").fillna(0.0)
    angle = 2.0 * np.pi * minute / 1440.0
    out["minute_sin"], out["minute_cos"] = np.sin(angle), np.cos(angle)
    for c in FEATURES:
        if c not in out.columns:
            out[c] = np.nan
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out[TARGET] = pd.to_numeric(out[TARGET], errors="coerce")
    out[RET_TARGET] = pd.to_numeric(out[RET_TARGET], errors="coerce")
    return out


def _split(frame: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    dates = [d for d in sorted(frame["trade_date"].dropna().unique()) if d and d != "NaT"]
    if len(dates) >= 3:
        train_dates, val_date, test_date = dates[:-2], dates[-2], dates[-1]
        return (
            frame[frame["trade_date"].isin(train_dates)].copy(),
            frame[frame["trade_date"] == val_date].copy(),
            frame[frame["trade_date"] == test_date].copy(),
            f"DAY_HOLDOUT train={train_dates[0]}..{train_dates[-1]} val={val_date} test={test_date}",
        )
    if len(dates) == 2:
        return (
            frame[frame["trade_date"] == dates[0]].copy(), pd.DataFrame(columns=frame.columns),
            frame[frame["trade_date"] == dates[1]].copy(), f"TWO_DAY_HOLDOUT train={dates[0]} test={dates[1]}",
        )
    n = len(frame); c1 = max(1, int(n * 0.70)); c2 = min(n - 1, max(c1 + 1, int(n * 0.85)))
    return frame.iloc[:c1].copy(), frame.iloc[c1:c2].copy(), frame.iloc[c2:].copy(), "PROVISIONAL_SINGLE_DAY_70_15_15"


def _edge(y_true: Sequence[int], y_pred: Sequence[int], returns: Sequence[float]) -> Dict[str, Any]:
    edges: List[float] = []; correct = directional = 0
    for yt, yp, r in zip(y_true, y_pred, returns):
        yp = int(yp)
        if yp not in (-1, 1) or not math.isfinite(float(r)):
            continue
        directional += 1
        edges.append(float(r) if yp == 1 else -float(r))
        correct += int(int(yt) == yp)
    return {
        "directional_predictions": directional,
        "directional_coverage_pct": (100.0 * directional / len(y_pred)) if len(y_pred) else None,
        "directional_accuracy_pct": (100.0 * correct / directional) if directional else None,
        "avg_directional_edge_pct": float(np.mean(edges)) if edges else None,
        "avg_directional_edge_bp": float(np.mean(edges) * 100.0) if edges else None,
    }


def _metrics(name: str, y: np.ndarray, pred: np.ndarray, ret: np.ndarray) -> Dict[str, Any]:
    return {
        "model": name,
        "n": int(len(y)),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)) if len(y) else None,
        "macro_f1": float(f1_score(y, pred, average="macro", zero_division=0)) if len(y) else None,
        "confusion_matrix_labels_-1_0_1": confusion_matrix(y, pred, labels=[-1, 0, 1]).tolist() if len(y) else [],
        "true_classes": {str(k): int(v) for k, v in pd.Series(y).value_counts().sort_index().items()},
        "pred_classes": {str(k): int(v) for k, v in pd.Series(pred).value_counts().sort_index().items()},
        **_edge(y, pred, ret),
    }


def _baseline(test: pd.DataFrame) -> Dict[str, Any]:
    mapping = {"DOWN": -1, "WATCH": 0, "UP": 1}
    pred = test["baseline_direction"].map(mapping).fillna(0).astype(int).to_numpy()
    y = test[TARGET].astype(int).to_numpy(); ret = test[RET_TARGET].astype(float).to_numpy()
    return _metrics("direction_v18_baseline", y, pred, ret)


def _models() -> Dict[str, Pipeline]:
    return {
        "logistic_balanced": Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(max_iter=2500, class_weight="balanced", C=0.5, solver="lbfgs")),
        ]),
        "hist_gradient_boosting": Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("model", HistGradientBoostingClassifier(
                learning_rate=0.05, max_iter=220, max_leaf_nodes=15,
                min_samples_leaf=30, l2_regularization=1.0, random_state=42,
            )),
        ]),
    }


def train(symbol: str, min_samples: int, data_root: Path) -> int:
    raw = _load_rows(data_root)
    if raw.empty:
        print("No training_samples_v2 data yet. Keep the L2 recorder running during market hours."); return 2
    frame = _expand(raw)
    frame = frame[(frame["valid"] == 1) & (frame["true_l2"] == 1) & frame[TARGET].isin([-1, 0, 1])].copy()
    if symbol.upper() != "ALL":
        frame = frame[frame["symbol"].str.upper() == symbol.upper()].copy()
    frame = frame.dropna(subset=[TARGET, RET_TARGET, "generated_ts"]).sort_values("generated_ts").reset_index(drop=True)
    if len(frame) < min_samples:
        print(f"Need >= {min_samples} valid true-L2 labels; found {len(frame)} for {symbol.upper()}."); return 2
    train_df, val_df, test_df, protocol = _split(frame)
    if train_df.empty or test_df.empty:
        print("Not enough chronological blocks yet."); return 2
    y_train = train_df[TARGET].astype(int).to_numpy()
    if len(set(y_train.tolist())) < 2:
        print("Training block contains fewer than two classes. Collect more data."); return 2

    X_train, X_test = train_df[FEATURES], test_df[FEATURES]
    y_test = test_df[TARGET].astype(int).to_numpy(); ret_test = test_df[RET_TARGET].astype(float).to_numpy()
    X_val = val_df[FEATURES] if len(val_df) else None
    y_val = val_df[TARGET].astype(int).to_numpy() if len(val_df) else None
    ret_val = val_df[RET_TARGET].astype(float).to_numpy() if len(val_df) else None

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S"); scope = symbol.upper().replace(".", "_")
    report: Dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": symbol.upper(), "target": TARGET, "protocol": protocol,
        "samples_total": len(frame), "samples_train": len(train_df), "samples_validation": len(val_df), "samples_test": len(test_df),
        "features": FEATURES, "baseline_test": _baseline(test_df), "models": {}, "auto_deployed": False,
    }
    for name, model in _models().items():
        model.fit(X_train, y_train)
        item: Dict[str, Any] = {"test": _metrics(name, y_test, model.predict(X_test), ret_test)}
        if X_val is not None and y_val is not None and len(y_val):
            item["validation"] = _metrics(name, y_val, model.predict(X_val), ret_val)
        path = MODEL_DIR / f"{scope}_{name}_{stamp}.joblib"
        joblib.dump({"model": model, "features": FEATURES, "target": TARGET, "scope": symbol.upper()}, path)
        item["model_path"] = str(path); report["models"][name] = item

    report_path = MODEL_DIR / f"{scope}_training_report_latest.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 78)
    print(f"AStock L2 60s ML | scope={symbol.upper()} | samples={len(frame)}")
    print(protocol)
    b = report["baseline_test"]
    print(f"baseline: bal_acc={b['balanced_accuracy']:.3f} dir_acc={b['directional_accuracy_pct']} edge_bp={b['avg_directional_edge_bp']}")
    for name, item in report["models"].items():
        m = item["test"]
        print(f"{name}: bal_acc={m['balanced_accuracy']:.3f} f1={m['macro_f1']:.3f} dir_acc={m['directional_accuracy_pct']} edge_bp={m['avg_directional_edge_bp']}")
    print(f"Report: {report_path}")
    print("No live deployment was performed.")
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=PRIMARY_SYMBOL)
    p.add_argument("--min-samples", type=int, default=200)
    p.add_argument("--data-root", default=str(DATA_ROOT))
    a = p.parse_args()
    raise SystemExit(train(a.symbol, a.min_samples, Path(a.data_root).expanduser()))


if __name__ == "__main__":
    main()
