# -*- coding: utf-8 -*-
"""iMACD research audit for the A-Stock 60-second project.

Research only.  The purpose is to test whether a causal MACD state engine plus
past-only ranking can turn the weak L1 ranking signal into positive 60s economic
edge.  It never changes V18, never auto-trades, and never tunes on a test day.

Anti-overfit choices are intentionally small/fixed:
- classic 12/26/9 proportions on 5s and 15s clocks only;
- four pre-declared ablations, with IMACD_FLOW_VWAP as the primary candidate;
- fixed past-only validation probability percentiles 80/90/95; q90 is primary;
- train is thinned to 15s, validation/test to non-overlapping 60s;
- exact historical ask-now -> future bid for UP and bid-now -> future bid for DOWN;
- 2bp extra execution hurdle; test never selects features, C, percentile, or state rules.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

VERSION = "imacd-state-ranking-audit-v1-20260906"
CLOUD_SCOPE = "QMT_L1_60S_IMACD_V1"
HURDLE_BP = 2.0
PRIMARY_VARIANT = "IMACD_FLOW_VWAP"
PRIMARY_QUANTILE = 0.90
QUANTILES = (0.80, 0.90, 0.95)

IMACD = [
    "imacd5_dif_pct", "imacd5_hist_pct", "imacd5_hist_slope", "imacd5_hist_accel",
    "imacd5_cross_age", "imacd5_whipsaw_120s",
    "imacd15_dif_pct", "imacd15_hist_pct", "imacd15_hist_slope",
    "imacd_multiscale_alignment", "imacd_state_up", "imacd_state_down",
    "imacd_up_exhaust", "imacd_down_exhaust",
]
VWAP = ["above_vwap_pct"]
FLOW = ["tick_buy_centered", "book_pressure_centered", "pressure_change_pct"]
VARIANTS = {
    "IMACD_ONLY": IMACD,
    "IMACD_VWAP": IMACD + VWAP,
    "IMACD_FLOW": IMACD + FLOW,
    PRIMARY_VARIANT: IMACD + FLOW + VWAP,
}


def _safe_auc(y: pd.Series, p: np.ndarray) -> Optional[float]:
    yy = pd.to_numeric(y, errors="coerce").dropna().astype(int)
    if len(yy) != len(p) or yy.nunique() < 2:
        return None
    try:
        return float(roc_auc_score(yy.to_numpy(), p))
    except Exception:
        return None


def _macd(price: pd.Series) -> pd.DataFrame:
    p = pd.to_numeric(price, errors="coerce").ffill()
    ema12 = p.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = p.ewm(span=26, adjust=False, min_periods=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False, min_periods=9).mean()
    hist = dif - dea
    denom = p.replace(0.0, np.nan)
    dif_pct = dif / denom * 100.0
    hist_pct = hist / denom * 100.0
    slope = hist_pct.diff(2)
    accel = slope.diff(2)
    sign = np.sign(hist_pct.fillna(0.0))
    changed = sign.ne(sign.shift(1)) & sign.ne(0)
    cross_id = changed.cumsum()
    age = hist_pct.groupby(cross_id).cumcount().astype(float)
    whipsaw = changed.astype(int).rolling(24, min_periods=1).sum().astype(float)
    return pd.DataFrame({
        "dif_pct": dif_pct, "hist_pct": hist_pct, "hist_slope": slope,
        "hist_accel": accel, "cross_age": age, "whipsaw": whipsaw,
    }, index=price.index)


def _add_imacd(frame: pd.DataFrame) -> pd.DataFrame:
    """Causal feature construction; no future/label columns are referenced."""
    if frame.empty:
        return frame.copy()
    pieces: List[pd.DataFrame] = []
    keys = ["symbol", "trade_date", "session"]
    for _, part0 in frame.sort_values("generated_ts").groupby(keys, sort=False, dropna=False):
        part = part0.copy().sort_values("generated_ts")
        m5 = _macd(part["last_price"])
        for c in ("dif_pct", "hist_pct", "hist_slope", "hist_accel", "cross_age", "whipsaw"):
            part[f"imacd5_{'whipsaw_120s' if c == 'whipsaw' else c}"] = m5[c].to_numpy()

        # 15-second clock is built only from observations available at or before each row.
        tmp = part[["generated_ts", "last_price"]].copy()
        tmp["slot15"] = (pd.to_numeric(tmp["generated_ts"], errors="coerce") // 15).astype("Int64")
        q = tmp.dropna(subset=["slot15"]).groupby("slot15", as_index=False).last()
        m15 = _macd(q["last_price"]).copy()
        q["imacd15_dif_pct"] = m15["dif_pct"].to_numpy()
        q["imacd15_hist_pct"] = m15["hist_pct"].to_numpy()
        q["imacd15_hist_slope"] = m15["hist_slope"].to_numpy()
        q = q[["generated_ts", "imacd15_dif_pct", "imacd15_hist_pct", "imacd15_hist_slope"]].sort_values("generated_ts")
        left = part.sort_values("generated_ts")
        part = pd.merge_asof(left, q, on="generated_ts", direction="backward")

        h5 = pd.to_numeric(part["imacd5_hist_pct"], errors="coerce")
        s5 = pd.to_numeric(part["imacd5_hist_slope"], errors="coerce")
        h15 = pd.to_numeric(part["imacd15_hist_pct"], errors="coerce")
        s15 = pd.to_numeric(part["imacd15_hist_slope"], errors="coerce")
        prev_h5 = h5.shift(1)
        cross_up = (h5 > 0) & (prev_h5 <= 0) & (s5 > 0)
        cross_dn = (h5 < 0) & (prev_h5 >= 0) & (s5 < 0)
        whipsaw = pd.to_numeric(part["imacd5_whipsaw_120s"], errors="coerce").fillna(0.0) >= 3
        accel_up = (h5 > 0) & (s5 > 0) & (h15 >= 0) & (s15 >= 0)
        accel_dn = (h5 < 0) & (s5 < 0) & (h15 <= 0) & (s15 <= 0)
        up_exhaust = (h5 > 0) & (s5 < 0)
        dn_exhaust = (h5 < 0) & (s5 > 0)
        state_up = (~whipsaw) & (cross_up | accel_up)
        state_dn = (~whipsaw) & (cross_dn | accel_dn)
        part["imacd_state_up"] = state_up.astype(float)
        part["imacd_state_down"] = state_dn.astype(float)
        part["imacd_up_exhaust"] = up_exhaust.astype(float)
        part["imacd_down_exhaust"] = dn_exhaust.astype(float)
        part["imacd_multiscale_alignment"] = np.sign(h5.fillna(0.0)) * np.sign(h15.fillna(0.0))
        part["imacd_state"] = np.select(
            [whipsaw, cross_up, cross_dn, accel_up, accel_dn, up_exhaust, dn_exhaust],
            ["WHIPSAW", "EARLY_UP", "EARLY_DOWN", "ACCEL_UP", "ACCEL_DOWN", "UP_EXHAUST", "DOWN_EXHAUST"],
            default="NEUTRAL",
        )
        pieces.append(part)
    out = pd.concat(pieces, ignore_index=True) if pieces else frame.copy()
    return out.sort_values(["generated_ts", "symbol"]).reset_index(drop=True)


def _read_dataset(root: Path, dates: Iterable[str]) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for d in dates:
        path = root / "training" / str(d) / "l2_training.sqlite3"
        if not path.exists():
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
        try:
            df = pd.read_sql_query(
                "SELECT symbol,generated_ts,generated_at,session,last_price,bid1,ask1,future_bid_60,"
                "ret_ask_to_bid_60_pct,features_json FROM training_samples_v2 WHERE valid=1",
                conn,
            )
        finally:
            conn.close()
        if df.empty:
            continue
        df["trade_date"] = str(d)
        fx: List[Dict[str, Any]] = []
        for text in df["features_json"].fillna("{}"):
            try:
                obj = json.loads(text) if isinstance(text, str) else {}
            except Exception:
                obj = {}
            fx.append(obj if isinstance(obj, dict) else {})
        ff = pd.DataFrame(fx, index=df.index)
        for c, default in (("above_vwap_pct", 0.0), ("tick_buy_pct", 50.0),
                           ("book_buy_pressure_pct", 50.0), ("pressure_change_pct", 0.0),
                           ("minute_of_day", np.nan)):
            df[c] = pd.to_numeric(ff[c], errors="coerce") if c in ff.columns else default
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    for c in ("generated_ts", "last_price", "bid1", "ask1", "future_bid_60", "ret_ask_to_bid_60_pct"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out["tick_buy_centered"] = pd.to_numeric(out["tick_buy_pct"], errors="coerce").fillna(50.0) - 50.0
    out["book_pressure_centered"] = pd.to_numeric(out["book_buy_pressure_pct"], errors="coerce").fillna(50.0) - 50.0
    out["down_hold_return_pct"] = np.where(
        (out["bid1"] > 0) & (out["future_bid_60"] > 0),
        (out["future_bid_60"] / out["bid1"] - 1.0) * 100.0,
        np.nan,
    )
    out["up_target"] = (out["ret_ask_to_bid_60_pct"] > HURDLE_BP / 100.0).astype(int)
    out["down_target"] = (out["down_hold_return_pct"] < -HURDLE_BP / 100.0).astype(int)
    return _add_imacd(out)


def _thin(df: pd.DataFrame, seconds: int) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    x = df.sort_values("generated_ts").copy()
    x["_thin"] = (pd.to_numeric(x["generated_ts"], errors="coerce") // seconds).astype("Int64")
    return x.drop_duplicates(["symbol", "trade_date", "session", "_thin"], keep="last").drop(columns="_thin")


def _model() -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", RobustScaler(quantile_range=(25.0, 75.0))),
        ("clf", LogisticRegression(C=0.10, class_weight="balanced", max_iter=1200, solver="lbfgs")),
    ])


def _evaluate(test: pd.DataFrame, up_p: np.ndarray, dn_p: np.ndarray, up_thr: float, dn_thr: float) -> Dict[str, Any]:
    up_state = pd.to_numeric(test["imacd_state_up"], errors="coerce").fillna(0).to_numpy() > 0.5
    dn_state = pd.to_numeric(test["imacd_state_down"], errors="coerce").fillna(0).to_numpy() > 0.5
    sig = np.zeros(len(test), dtype=int)
    sig[up_state & (up_p >= up_thr)] = 1
    sig[dn_state & (dn_p >= dn_thr)] = -1
    idx = sig != 0
    n = int(idx.sum())
    total = int(len(test))
    up_ret = pd.to_numeric(test["ret_ask_to_bid_60_pct"], errors="coerce").to_numpy(float)
    dn_ret = pd.to_numeric(test["down_hold_return_pct"], errors="coerce").to_numpy(float)
    correct = np.zeros(len(test), dtype=bool)
    correct[sig == 1] = up_ret[sig == 1] > HURDLE_BP / 100.0
    correct[sig == -1] = dn_ret[sig == -1] < -HURDLE_BP / 100.0
    gross = np.full(len(test), np.nan, dtype=float)
    gross[sig == 1] = up_ret[sig == 1] * 100.0
    gross[sig == -1] = -dn_ret[sig == -1] * 100.0
    directional_gross = gross[idx]
    valid_edge = directional_gross[np.isfinite(directional_gross)]
    acc = float(correct[idx].mean() * 100.0) if n else None
    net = float(valid_edge.mean() - HURDLE_BP) if len(valid_edge) else None
    gross_bp = float(valid_edge.mean()) if len(valid_edge) else None
    minute = pd.to_numeric(test.get("minute_of_day"), errors="coerce").to_numpy(float)
    opening = idx & (minute >= 570) & (minute < 630)
    on = int(opening.sum())
    opening_net_vals = gross[opening]
    opening_net_vals = opening_net_vals[np.isfinite(opening_net_vals)]
    return {
        "n": total, "directional_predictions": n,
        "directional_accuracy_pct": acc,
        "directional_coverage_pct": (100.0 * n / total) if total else None,
        "avg_gross_edge_bp": gross_bp, "avg_net_edge_bp": net,
        "up_predictions": int((sig == 1).sum()), "down_predictions": int((sig == -1).sum()),
        "opening": {
            "directional_predictions": on,
            "directional_accuracy_pct": float(correct[opening].mean() * 100.0) if on else None,
            "avg_net_edge_bp": float(opening_net_vals.mean() - HURDLE_BP) if len(opening_net_vals) else None,
        },
    }


def _fit_fold(frame: pd.DataFrame, history_dates: List[str], variant: str) -> Dict[str, Any]:
    features = list(VARIANTS[variant])
    train_days, val_day, test_day = history_dates[:-2], history_dates[-2], history_dates[-1]
    tr = _thin(frame[frame["trade_date"].isin(train_days)].copy(), 15)
    va = _thin(frame[frame["trade_date"] == val_day].copy(), 60)
    te = _thin(frame[frame["trade_date"] == test_day].copy(), 60)
    # MACD needs enough warm-up and exact future bid economics.
    needed = features + ["up_target", "down_target", "ret_ask_to_bid_60_pct", "down_hold_return_pct"]
    tr = tr.dropna(subset=[c for c in needed if c in tr.columns])
    va = va.dropna(subset=["ret_ask_to_bid_60_pct", "down_hold_return_pct"])
    te = te.dropna(subset=["ret_ask_to_bid_60_pct", "down_hold_return_pct"])
    if len(tr) < 300 or len(va) < 100 or len(te) < 100:
        return {"test_day": test_day, "error": "insufficient_rows", "train": len(tr), "val": len(va), "test": len(te)}
    if tr["up_target"].nunique() < 2 or tr["down_target"].nunique() < 2:
        return {"test_day": test_day, "error": "single_class_training_target"}

    up = _model(); dn = _model()
    up.fit(tr[features], tr["up_target"].astype(int))
    dn.fit(tr[features], tr["down_target"].astype(int))
    vup = up.predict_proba(va[features])[:, 1]; vdn = dn.predict_proba(va[features])[:, 1]
    tup = up.predict_proba(te[features])[:, 1]; tdn = dn.predict_proba(te[features])[:, 1]
    up_aligned = vup[pd.to_numeric(va["imacd_state_up"], errors="coerce").fillna(0).to_numpy() > 0.5]
    dn_aligned = vdn[pd.to_numeric(va["imacd_state_down"], errors="coerce").fillna(0).to_numpy() > 0.5]
    curves: Dict[str, Any] = {}
    for q in QUANTILES:
        # Thresholds come from the past validation day only; the test distribution is never ranked/looked at.
        uth = float(np.quantile(up_aligned, q)) if len(up_aligned) >= 30 else 1.1
        dth = float(np.quantile(dn_aligned, q)) if len(dn_aligned) >= 30 else 1.1
        curves[f"q{int(q*100)}"] = {
            "up_threshold_from_validation": uth,
            "down_threshold_from_validation": dth,
            "metrics": _evaluate(te, tup, tdn, uth, dth),
        }
    primary = curves["q90"]["metrics"]
    primary["up_auc"] = _safe_auc(te["up_target"].astype(int), tup)
    primary["down_auc"] = _safe_auc(te["down_target"].astype(int), tdn)
    return {
        "test_day": test_day, "train_days": train_days, "validation_day": val_day,
        "features": features, "train_rows_15s": int(len(tr)), "validation_rows_60s": int(len(va)),
        "test_rows_60s": int(len(te)), "primary_q": PRIMARY_QUANTILE,
        "primary": primary, "quantile_diagnostics": curves,
    }


def _aggregate(folds: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [f.get("primary") for f in folds if isinstance(f.get("primary"), dict)]
    n = sum(int(x.get("directional_predictions") or 0) for x in rows)
    total = sum(int(x.get("n") or 0) for x in rows)
    correct = sum((float(x.get("directional_accuracy_pct") or 0.0) / 100.0) * int(x.get("directional_predictions") or 0) for x in rows)
    net_num = sum(float(x.get("avg_net_edge_bp") or 0.0) * int(x.get("directional_predictions") or 0) for x in rows)
    gross_num = sum(float(x.get("avg_gross_edge_bp") or 0.0) * int(x.get("directional_predictions") or 0) for x in rows)
    pos_days = sum(1 for x in rows if x.get("avg_net_edge_bp") is not None and float(x["avg_net_edge_bp"]) > 0)
    acc = 100.0 * correct / n if n else None
    wilson = None
    if n:
        p = correct / n; z = 1.96; den = 1 + z*z/n
        center = (p + z*z/(2*n)) / den
        half = z * math.sqrt((p*(1-p) + z*z/(4*n))/n) / den
        wilson = 100.0 * max(0.0, center - half)
    return {
        "folds": len(rows), "test_rows": total, "directional_predictions": n,
        "directional_accuracy_pct": acc, "accuracy_wilson_95_lower_pct": wilson,
        "directional_coverage_pct": 100.0 * n / total if total else None,
        "avg_gross_edge_bp": gross_num / n if n else None,
        "avg_net_edge_bp": net_num / n if n else None,
        "positive_net_edge_days": pos_days,
        "daily": [{"day": f.get("test_day"), **(f.get("primary") or {})} for f in folds if f.get("primary")],
    }


def _state_audit(frame: pd.DataFrame, dates: List[str]) -> Dict[str, Any]:
    test_days = dates[-5:]
    x = _thin(frame[frame["trade_date"].isin(test_days)].copy(), 60)
    out: Dict[str, Any] = {}
    for state, g in x.groupby("imacd_state", dropna=False):
        up = pd.to_numeric(g["ret_ask_to_bid_60_pct"], errors="coerce").dropna() * 100.0
        dn = -pd.to_numeric(g["down_hold_return_pct"], errors="coerce").dropna() * 100.0
        out[str(state)] = {
            "n": int(len(g)),
            "avg_up_ask_to_future_bid_bp": float(up.mean()) if len(up) else None,
            "up_gt_2bp_pct": float((up > HURDLE_BP).mean() * 100.0) if len(up) else None,
            "avg_down_avoidance_bp": float(dn.mean()) if len(dn) else None,
            "down_gt_2bp_pct": float((dn > HURDLE_BP).mean() * 100.0) if len(dn) else None,
        }
    return out


def _gate(m: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    acc = m.get("directional_accuracy_pct"); net = m.get("avg_net_edge_bp"); cov = m.get("directional_coverage_pct")
    base_acc = baseline.get("directional_accuracy_pct"); base_net = baseline.get("avg_net_edge_bp")
    checks = {
        "directional_predictions_ge_100": int(m.get("directional_predictions") or 0) >= 100,
        "accuracy_ge_55pct": acc is not None and float(acc) >= 55.0,
        "coverage_ge_5pct": cov is not None and float(cov) >= 5.0,
        "net_edge_positive": net is not None and float(net) > 0.0,
        "positive_net_edge_days_ge_3": int(m.get("positive_net_edge_days") or 0) >= 3,
        "wilson_lower_ge_52pct": m.get("accuracy_wilson_95_lower_pct") is not None and float(m["accuracy_wilson_95_lower_pct"]) >= 52.0,
        "beats_v4r_accuracy": acc is not None and base_acc is not None and float(acc) > float(base_acc),
        "beats_v4r_net_edge": net is not None and base_net is not None and float(net) > float(base_net),
    }
    return {"pass": all(checks.values()), "checks": checks}


def _sync(report: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from modules.cloud_bridge import CloudBridge, load_bridge_config
        cfg = load_bridge_config(); bridge = CloudBridge(cfg, timeout=20.0)
        primary = ((report.get("variants") or {}).get(PRIMARY_VARIANT) or {}).get("aggregate") or {}
        payload = {
            "bridge_id": cfg.bridge_id, "scope": CLOUD_SCOPE, "trainer_version": VERSION,
            "generated_at": report["generated_at"], "maturity": "DEVELOPMENT_BACKTEST_NOT_PRISTINE_OOS",
            "protocol": "QMT_TICK_5S__IMACD_12_26_9_MULTI_CLOCK__VAL_RANK_Q90__EXACT_FUTURE_BID__2BP",
            "samples_total": int(report.get("samples_total") or 0),
            "samples_test_nonoverlap": int(primary.get("test_rows") or 0), "report": report,
        }
        bridge._request("POST", "ml_training_reports_v1?on_conflict=bridge_id,scope,generated_at", json=payload,
                        headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return {"ok": True, "scope": CLOUD_SCOPE}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def run_audit(dataset_root: Path, dates: List[str], baseline_v4r: Dict[str, Any], generated_at: str) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "version": VERSION, "generated_at": generated_at, "dates": dates,
        "qualification": "DEVELOPMENT_BACKTEST_NOT_PRISTINE_OOS",
        "test_used_for_selection": False, "primary_variant": PRIMARY_VARIANT,
        "primary_quantile": PRIMARY_QUANTILE, "fixed_macd_clocks": ["5s:12/26/9", "15s:12/26/9"],
        "execution_hurdle_bp": HURDLE_BP,
        "overfit_controls": ["four_predeclared_ablations", "q90_primary_fixed", "validation_only_rank_cutoffs", "60s_nonoverlap_val_test", "exact_future_bid_economics"],
        "eligible_for_live_deployment": False,
    }
    try:
        frame = _read_dataset(dataset_root, dates)
        report["samples_total"] = int(len(frame))
        report["state_audit"] = _state_audit(frame, dates)
        test_indices = list(range(5 if len(dates) >= 6 else len(dates)-1, len(dates)))
        if len(test_indices) > 5:
            test_indices = test_indices[-5:]
        variants: Dict[str, Any] = {}
        for variant in VARIANTS:
            folds = [_fit_fold(frame, dates[:idx+1], variant) for idx in test_indices]
            variants[variant] = {"folds": folds, "aggregate": _aggregate(folds)}
        report["variants"] = variants
        primary = variants[PRIMARY_VARIANT]["aggregate"]
        report["baseline_v4r"] = {
            k: baseline_v4r.get(k) for k in ("directional_accuracy_pct", "directional_coverage_pct", "avg_net_edge_bp", "directional_predictions", "positive_net_edge_days")
        }
        report["development_gate"] = _gate(primary, baseline_v4r)
        report["shadow_candidate"] = bool(report["development_gate"]["pass"])
        report["interpretation"] = "A passing result may enter research/shadow only. Prospective unseen sessions remain mandatory before any V18 production change."
    except Exception as exc:
        report["fatal_error"] = f"{type(exc).__name__}: {exc}"
        report["shadow_candidate"] = False
    report["cloud_sync"] = _sync(report)
    return report
