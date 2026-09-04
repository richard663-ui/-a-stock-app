# -*- coding: utf-8 -*-
"""Robust L1/Tick 60s trainer V3.

Primary path reuses V2. If any legacy wrapper stage raises, V3 falls back to a
self-contained L1 trainer so research cannot be blocked by compatibility glue.
All failures print a compact stage + traceback tail for the auto-daemon to sync.
Research only; never auto-deploys.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.utils.class_weight import compute_sample_weight

import services.train_l1_60s_model_v2 as legacy
import services.train_l1_60s_model_v1 as l1
import services.train_l2_60s_model_v3 as splitbase

TRAINER_VERSION = "l1-60s-trainer-v3-robust-standalone-20260904"
DATA_ROOT = Path.home() / "AStockData"
MODEL_DIR = DATA_ROOT / "models" / "l1_60s"
RET_TARGET = "ret_smoothed_mid_60_pct"
TARGET = "label_actionable_smoothed_mid_60"
FEATURES = list(l1.L1_FEATURES)
PROB_THRESHOLDS = (0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)


def _load(data_root: Path) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for path in sorted((data_root / "training").glob("*/l2_training.sqlite3")):
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
        try:
            ok = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_samples_v2'").fetchone()
            if not ok:
                continue
            df = pd.read_sql_query(
                "SELECT * FROM training_samples_v2 WHERE labeled_at IS NOT NULL AND valid=1 ORDER BY generated_ts", conn
            )
            if not df.empty:
                frames.append(df)
        finally:
            conn.close()
    if not frames:
        return pd.DataFrame()
    raw = pd.concat(frames, ignore_index=True).sort_values("generated_ts").reset_index(drop=True)
    if "session" in raw.columns:
        raw = raw[raw["session"].astype(str).str.upper() != "OPEN_AUCTION"].copy()
    if "sample_bucket" in raw.columns:
        raw = raw.drop_duplicates(["symbol", "sample_bucket"], keep="last").reset_index(drop=True)
    return raw


def _expand(raw: pd.DataFrame) -> pd.DataFrame:
    records: List[Dict[str, Any]] = []
    for value in raw.get("features_json", pd.Series("{}", index=raw.index)).fillna("{}"):
        try:
            obj = json.loads(value) if isinstance(value, str) else dict(value or {})
        except Exception:
            obj = {}
        records.append(obj if isinstance(obj, dict) else {})
    fx = pd.DataFrame(records, index=raw.index)
    out = raw.copy().reset_index(drop=True)
    fx = fx.reset_index(drop=True)
    # Explicit merge: JSON sampled features win, table columns remain fallback.
    for c in fx.columns:
        out[c] = fx[c]
    out = out.loc[:, ~out.columns.duplicated(keep="last")].copy()

    out["generated_ts"] = pd.to_numeric(out.get("generated_ts"), errors="coerce")
    dt = pd.to_datetime(out.get("generated_at"), errors="coerce")
    out["trade_date"] = dt.dt.date.astype(str)

    def num(name: str, default: float = 0.0) -> pd.Series:
        if name not in out.columns:
            return pd.Series(default, index=out.index, dtype=float)
        return pd.to_numeric(out[name], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(default)

    minute = num("minute_of_day")
    out["session_am"] = ((minute >= 570) & (minute < 690)).astype(float)
    angle = 2.0 * np.pi * minute / 1440.0
    out["minute_sin"], out["minute_cos"] = np.sin(angle), np.cos(angle)
    out["phase_open_core"] = ((minute >= 570) & (minute < 630)).astype(float)
    out["phase_am_late"] = ((minute >= 630) & (minute < 690)).astype(float)
    out["phase_pm"] = ((minute >= 780) & (minute < 900)).astype(float)

    group_cols = ["symbol"] + (["trade_date"] if "trade_date" in out.columns else [])
    for src, dst in (
        ("volume", "volume_delta_5s"), ("amount", "amount_delta_5s"),
        ("spread_pct", "spread_change_5s"),
        ("depth5_imbalance_pct", "depth5_imbalance_change_5s"),
        ("microprice_vs_mid_pct", "microprice_change_5s"),
        ("tick_buy_pct", "tick_buy_change_5s"),
        ("book_buy_pressure_pct", "book_pressure_change_5s"),
    ):
        s = num(src)
        out[src] = s
        out[dst] = out.groupby(group_cols, sort=False, dropna=False)[src].diff().fillna(0.0)
    out["volume_delta_5s"] = out["volume_delta_5s"].clip(lower=0.0)
    out["amount_delta_5s"] = out["amount_delta_5s"].clip(lower=0.0)
    out["log_volume_delta_5s"] = np.log1p(out["volume_delta_5s"])
    out["log_amount_delta_5s"] = np.log1p(out["amount_delta_5s"])
    out["volume_accel_5s"] = out.groupby(group_cols, sort=False, dropna=False)["volume_delta_5s"].diff().fillna(0.0)
    out["amount_accel_5s"] = out.groupby(group_cols, sort=False, dropna=False)["amount_delta_5s"].diff().fillna(0.0)

    c10, c30, c60 = num("change_10s_pct"), num("change_30s_pct"), num("change_60s_pct")
    out["momentum_accel_10_30"] = c10 - c30 / 3.0
    out["momentum_accel_30_60"] = c30 - c60 / 2.0
    out["momentum_alignment_10_60"] = np.sign(c10 * c60)
    out["short_move_abs_pct"] = c10.abs() + c30.abs() / 3.0

    ret = pd.to_numeric(out.get(RET_TARGET), errors="coerce").replace([np.inf, -np.inf], np.nan)
    threshold = pd.to_numeric(out.get("label_threshold_pct"), errors="coerce")
    if not isinstance(threshold, pd.Series):
        threshold = pd.Series(np.nan, index=out.index)
    out[RET_TARGET] = ret
    out["label_threshold_pct"] = threshold.fillna(0.01)
    return out


def _models() -> Dict[str, Pipeline]:
    return {
        "logistic_balanced": Pipeline([
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(max_iter=3000, class_weight="balanced", C=0.5, solver="lbfgs")),
        ]),
        "hist_gradient_boosting": Pipeline([
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("model", HistGradientBoostingClassifier(
                learning_rate=0.04, max_iter=260, max_leaf_nodes=15,
                min_samples_leaf=30, l2_regularization=1.5, random_state=42,
            )),
        ]),
    }


def _predict(model: Pipeline, X: pd.DataFrame, threshold: float) -> np.ndarray:
    proba = model.predict_proba(X)
    classes = np.asarray(model.classes_, dtype=int)
    idx = np.argmax(proba, axis=1)
    cls, conf = classes[idx], proba[np.arange(len(proba)), idx]
    return np.where((conf >= threshold) & np.isin(cls, [-1, 1]), cls, 0).astype(int)


def _metrics(frame: pd.DataFrame, pred: np.ndarray, hurdle_bp: float) -> Dict[str, Any]:
    y = frame[TARGET].astype(int).to_numpy()
    ret = frame[RET_TARGET].astype(float).to_numpy()
    mask = np.isin(pred, [-1, 1]) & np.isfinite(ret)
    n = int(mask.sum())
    if n:
        signed = np.where(pred[mask] == 1, ret[mask], -ret[mask]) * 100.0
        acc = float((pred[mask] == y[mask]).mean() * 100.0)
        gross = float(np.mean(signed))
    else:
        acc = gross = None
    return {
        "n": int(len(frame)), "directional_predictions": n,
        "directional_coverage_pct": 100.0 * n / len(frame) if len(frame) else None,
        "directional_accuracy_pct": acc, "avg_gross_edge_bp": gross,
        "execution_hurdle_bp": float(hurdle_bp),
        "avg_net_edge_bp": (gross - hurdle_bp) if gross is not None else None,
    }


def _choose(model: Pipeline, val: pd.DataFrame, hurdle_bp: float) -> Tuple[float, Dict[str, Any]]:
    if val.empty:
        return 0.65, {}
    candidates = []
    for t in PROB_THRESHOLDS:
        m = _metrics(val, _predict(model, val[FEATURES], t), hurdle_bp)
        candidates.append((t, m))
    min_n = max(5, int(math.ceil(len(val) * 0.10)))
    elig = [(t, m) for t, m in candidates if int(m.get("directional_predictions") or 0) >= min_n]
    if not elig:
        return 0.65, {}
    def score(item):
        _, m = item
        edge = float(m.get("avg_net_edge_bp") if m.get("avg_net_edge_bp") is not None else -1e9)
        n = int(m.get("directional_predictions") or 0)
        return edge * math.sqrt(max(1, n)), float(m.get("directional_accuracy_pct") or 0.0), n
    return max(elig, key=score)


def _fallback_train(symbol: str, min_samples: int, data_root: Path, hurdle_bp: float) -> int:
    raw = _load(data_root)
    if raw.empty:
        print("V3_STAGE=load NO_DATA")
        return 2
    frame = _expand(raw)
    if symbol.upper() != "ALL":
        frame = frame[frame["symbol"].astype(str).str.upper() == symbol.upper()].copy()
    hurdle_pct = max(0.0, float(hurdle_bp)) / 100.0
    effective = np.maximum(frame["label_threshold_pct"].to_numpy(float), hurdle_pct)
    ret = frame[RET_TARGET].to_numpy(float)
    frame[TARGET] = np.where(ret > effective, 1, np.where(ret < -effective, -1, 0)).astype(int)
    frame = frame.dropna(subset=[RET_TARGET, "generated_ts"]).sort_values("generated_ts").reset_index(drop=True)
    for c in FEATURES:
        if c not in frame.columns:
            frame[c] = np.nan
        frame[c] = pd.to_numeric(frame[c], errors="coerce").replace([np.inf, -np.inf], np.nan)
    if len(frame) < min_samples:
        print(f"V3_STAGE=filter NEED_SAMPLES found={len(frame)} required={min_samples}")
        return 2

    train_df, val_df, test_df, meta = splitbase._split(frame)
    if train_df.empty or test_df.empty:
        print("V3_STAGE=split EMPTY_TRAIN_OR_TEST")
        return 2
    non_val = splitbase._nonoverlap(val_df) if not val_df.empty else val_df.copy()
    non_test = splitbase._nonoverlap(test_df)
    y_train = train_df[TARGET].astype(int).to_numpy()
    if len(set(y_train.tolist())) < 2:
        print("V3_STAGE=fit FEWER_THAN_TWO_CLASSES")
        return 2

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    scope = symbol.upper().replace(".", "_")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report: Dict[str, Any] = {
        "trainer_version": TRAINER_VERSION, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": symbol.upper(), "data_mode": "L1_BASELINE", "target": TARGET,
        "return_target": RET_TARGET, "execution_hurdle_bp": hurdle_bp,
        **meta, "samples_total": int(len(frame)), "samples_train": int(len(train_df)),
        "samples_validation_nonoverlap": int(len(non_val)), "samples_test_nonoverlap": int(len(non_test)),
        "features": FEATURES, "models": {}, "auto_deployed": False, "eligible_for_live_deployment": False,
        "fallback_engine_used": True,
    }
    for name, model in _models().items():
        if name == "hist_gradient_boosting":
            w = compute_sample_weight(class_weight="balanced", y=y_train)
            model.fit(train_df[FEATURES], y_train, model__sample_weight=w)
        else:
            model.fit(train_df[FEATURES], y_train)
        threshold, selected_val = _choose(model, non_val, hurdle_bp)
        test_metrics = _metrics(non_test, _predict(model, non_test[FEATURES], threshold), hurdle_bp)
        path = MODEL_DIR / f"{scope}_{name}_{stamp}.joblib"
        joblib.dump({"model": model, "features": FEATURES, "scope": symbol.upper(),
                     "trainer_version": TRAINER_VERSION, "selected_probability_threshold": threshold}, path)
        report["models"][name] = {
            "selected_probability_threshold": threshold,
            "threshold_source": "VALIDATION_NONOVERLAP" if not non_val.empty else "PRESET_NO_VALIDATION",
            "selected_validation_point": selected_val,
            "selected_threshold_test_nonoverlap": test_metrics,
            "model_path": str(path),
            "readiness": {"eligible_for_shadow_review": False, "eligible_for_live_deployment": False,
                          "reasons": ["research_only_v3_requires_multi_day_review"]},
        }
    report["any_model_eligible_for_shadow_review"] = False
    path = MODEL_DIR / f"{scope}_training_report_latest.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"V3_FALLBACK_SUCCESS scope={symbol.upper()} samples={len(frame)} test_nonoverlap={len(non_test)}")
    for name, item in report["models"].items():
        m = item["selected_threshold_test_nonoverlap"]
        print(f"{name}: threshold={item['selected_probability_threshold']:.2f} n={m.get('directional_predictions')} acc={m.get('directional_accuracy_pct')} net={m.get('avg_net_edge_bp')}bp")
    print(f"Report: {path}")
    return 0


def train(symbol: str, min_samples: int, data_root: Path, hurdle_bp: float) -> int:
    try:
        rc = int(legacy.train(symbol, min_samples, data_root, hurdle_bp))
        if rc == 0:
            print("V3_PRIMARY_SUCCESS legacy-compatible path completed.")
            return 0
        if rc == 2:
            return 2
        print(f"V3_PRIMARY_NONZERO rc={rc}; trying standalone fallback.")
    except Exception as exc:
        tail = traceback.format_exc()[-5000:]
        print(f"V3_PRIMARY_EXCEPTION {type(exc).__name__}: {exc}\n{tail}")
    try:
        return _fallback_train(symbol, min_samples, data_root, hurdle_bp)
    except Exception as exc:
        tail = traceback.format_exc()[-6000:]
        print(f"V3_FALLBACK_EXCEPTION {type(exc).__name__}: {exc}\n{tail}")
        return 1


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
