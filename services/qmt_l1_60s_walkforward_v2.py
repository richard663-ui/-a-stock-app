# -*- coding: utf-8 -*-
"""QMT L1 60s historical walk-forward audit V2.

V2 intentionally does NOT change V4R/V5R model parameters.  It repairs the
research audit around them:
- unambiguous V4R/V5R logs (V5 internally reuses the V4 core trainer);
- saved-report invariants so a wrong trainer/report cannot silently enter an
  aggregate;
- historical-vs-live parity diagnostics on overlapping 5-second buckets;
- a threshold-independent shuffled-label ROC-AUC null control, so an abstaining
  challenger cannot make the leakage control vacuous;
- separate cloud scope from V1; nothing can promote/deploy a model.

Historical replay remains reference-only because the model architecture was
informed by some Sep-02..Sep-04 observations.  Prospective unseen days remain
the qualification test.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

import services.qmt_l1_60s_walkforward_v1 as base

VERSION = "qmt-l1-60s-walkforward-v2-parity-null-20260905"
OUT_ROOT = Path.home() / "AStockData" / "qmt_l1_walkforward_v2"
LIVE_ROOT = Path.home() / "AStockData"
CLOUD_SCOPE = "QMT_L1_60S_WALKFORWARD_V2"
HURDLE_BP = base.HURDLE_BP

PARITY_FEATURES = [
    "minute_of_day", "spread_pct", "depth5_imbalance_pct", "microprice_vs_mid_pct",
    "day_return_pct", "distance_high_pct", "distance_low_pct",
    "change_10s_pct", "change_30s_pct", "change_60s_pct", "change_120s_pct",
    "above_vwap_pct", "tick_buy_pct", "book_buy_pressure_pct", "pressure_change_pct",
    "market_hs300_return_pct", "market_chinext_return_pct",
    "relative_to_hs300_pct", "relative_to_chinext_pct",
]
PCT_0_100 = {"depth5_imbalance_pct", "tick_buy_pct", "book_buy_pressure_pct", "pressure_change_pct"}
PARITY_SCALE_FLOOR = {
    "minute_of_day": 0.1,
    "spread_pct": 0.005,
    "microprice_vs_mid_pct": 0.005,
    **{x: 2.0 for x in PCT_0_100},
}


def _capture(label: str, fn, *args, **kwargs) -> int:
    """Run a trainer but label every emitted line with its real audit variant."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = int(fn(*args, **kwargs))
    for line in buf.getvalue().splitlines():
        if line.strip():
            print(f"[{label}] {line}")
    return rc


def _thresholds_ok(report: Dict[str, Any], variant: str) -> Tuple[bool, List[str]]:
    problems: List[str] = []
    expected = "l1-60s-trainer-v4r-" if variant == "V4R" else "l1-60s-trainer-v5r-"
    version = str(report.get("trainer_version") or "")
    if expected not in version:
        problems.append(f"trainer_version_mismatch:{version}")
    for family, item in (report.get("models") or {}).items():
        th = item.get("selected_probability_threshold") or {}
        for head in ("up_entry", "down_risk"):
            try:
                value = float(th.get(head))
            except Exception:
                problems.append(f"{family}:{head}:missing_threshold")
                continue
            if variant == "V4R":
                if value < 0.50 - 1e-9 or value > 0.90 + 1e-9:
                    problems.append(f"{family}:{head}:v4_threshold_out_of_range:{value}")
            else:
                if not (0.55 - 1e-9 <= value <= 0.90 + 1e-9 or abs(value - 0.999) < 1e-9):
                    problems.append(f"{family}:{head}:v5_threshold_out_of_range:{value}")
    return not problems, problems


def _run_fold(dataset_root: Path, audit_root: Path, dates: List[str], fold_no: int) -> Dict[str, Any]:
    import services.train_l1_60s_model_v4 as core
    import services.train_l1_60s_model_v4r as v4r
    import services.train_l1_60s_model_v5_challenger as v5
    import services.train_l1_60s_model_v5r as v5r

    test_day = dates[-1]
    fold_root = audit_root / f"fold_{fold_no:02d}_{test_day}"
    data_root = fold_root / "data"
    models_v4 = fold_root / "models_v4"
    models_v5 = fold_root / "models_v5"
    base._copy_fold(dataset_root, data_root, dates)

    core.MODEL_DIR = models_v4
    v4r.MODEL_DIR = models_v4
    v5.MODEL_DIR = models_v5
    v5r.MODEL_DIR = models_v5

    print(f"[FOLD {fold_no}] history through {dates[-2]} -> frozen TEST {test_day}")
    rc4 = _capture("V4R", v4r.train, "ALL", 600, data_root, HURDLE_BP)
    rc5 = _capture("V5R", v5r.train, "ALL", 600, data_root, HURDLE_BP)
    r4 = base._load_json(models_v4 / "ALL_training_report_latest.json")
    r5 = base._load_json(models_v5 / "ALL_training_report_latest.json")

    ok4, p4 = _thresholds_ok(r4, "V4R")
    ok5, p5 = _thresholds_ok(r5, "V5R")
    if rc4 == 0 and not ok4:
        raise RuntimeError("V4R audit invariant failed: " + ";".join(p4))
    if rc5 == 0 and not ok5:
        raise RuntimeError("V5R audit invariant failed: " + ";".join(p5))

    prepared = core._prepare("ALL", data_root, HURDLE_BP)
    for item in (r4.get("models") or {}).values():
        item["exact_up_entry_execution"] = base._exact_execution(item, prepared, "V4R")
    for item in (r5.get("models") or {}).values():
        item["exact_up_entry_execution"] = base._exact_execution(item, prepared, "V5R")

    return {
        "fold": fold_no, "history_dates": dates, "test_day": test_day,
        "v4r_rc": rc4, "v5r_rc": rc5,
        "audit_invariants": {"v4r": {"ok": ok4, "problems": p4}, "v5r": {"ok": ok5, "problems": p5}},
        "v4r": r4, "v5r": r5,
    }


def _read_training_rows(root: Path, dates: Iterable[str]) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for d in dates:
        path = root / "training" / str(d) / "l2_training.sqlite3"
        if not path.exists():
            continue
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
            cols = {str(x[1]) for x in conn.execute("PRAGMA table_info(training_samples_v2)").fetchall()}
            need = {"symbol", "sample_bucket", "generated_ts", "features_json"}
            if not need.issubset(cols):
                conn.close(); continue
            extra = ", ret_smoothed_mid_60_pct" if "ret_smoothed_mid_60_pct" in cols else ", NULL as ret_smoothed_mid_60_pct"
            valid_clause = " WHERE valid=1" if "valid" in cols else ""
            df = pd.read_sql_query(
                "SELECT symbol,sample_bucket,generated_ts,features_json" + extra + " FROM training_samples_v2" + valid_clause,
                conn,
            )
            conn.close()
            if not df.empty:
                df["trade_date"] = str(d)
                frames.append(df)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["generated_ts"] = pd.to_numeric(out["generated_ts"], errors="coerce")
    out["sample_bucket"] = pd.to_numeric(out["sample_bucket"], errors="coerce")
    out = out.dropna(subset=["symbol", "sample_bucket", "generated_ts"])
    return out.sort_values("generated_ts").drop_duplicates(["symbol", "sample_bucket"], keep="last").reset_index(drop=True)


def _feature_table(rows: pd.DataFrame) -> pd.DataFrame:
    records: List[Dict[str, Any]] = []
    for value in rows.get("features_json", pd.Series("{}", index=rows.index)).fillna("{}"):
        try:
            obj = json.loads(value) if isinstance(value, str) else dict(value or {})
        except Exception:
            obj = {}
        records.append(obj if isinstance(obj, dict) else {})
    fx = pd.DataFrame(records, index=rows.index)
    out = rows[["symbol", "sample_bucket", "trade_date", "ret_smoothed_mid_60_pct"]].copy().reset_index(drop=True)
    fx = fx.reset_index(drop=True)
    for c in PARITY_FEATURES:
        out[c] = pd.to_numeric(fx[c], errors="coerce") if c in fx.columns else np.nan
    out["ret_smoothed_mid_60_pct"] = pd.to_numeric(out["ret_smoothed_mid_60_pct"], errors="coerce")
    return out


def _parity_metric(h: pd.Series, l: pd.Series, name: str) -> Dict[str, Any]:
    pair = pd.DataFrame({"h": pd.to_numeric(h, errors="coerce"), "l": pd.to_numeric(l, errors="coerce")}).dropna()
    n = int(len(pair))
    if not n:
        return {"n": 0}
    diff = (pair["h"] - pair["l"]).abs()
    hiqr = float(pair["h"].quantile(0.75) - pair["h"].quantile(0.25))
    liqr = float(pair["l"].quantile(0.75) - pair["l"].quantile(0.25))
    floor = float(PARITY_SCALE_FLOOR.get(name, 0.02))
    scale = max(abs(hiqr), abs(liqr), floor)
    pearson = pair["h"].corr(pair["l"], method="pearson") if n >= 3 else np.nan
    spearman = pair["h"].corr(pair["l"], method="spearman") if n >= 3 else np.nan
    smad = float(diff.median() / scale)
    severe = bool(n >= 100 and ((math.isfinite(float(spearman)) and abs(float(spearman)) < 0.35 and smad > 1.0) or smad > 2.0))
    return {
        "n": n,
        "historical_median": float(pair["h"].median()), "live_median": float(pair["l"].median()),
        "median_abs_diff": float(diff.median()), "robust_scale": scale, "scaled_median_abs_diff": smad,
        "pearson": float(pearson) if pd.notna(pearson) else None,
        "spearman": float(spearman) if pd.notna(spearman) else None,
        "severe_mismatch": severe,
    }


def _historical_live_parity(dataset_root: Path, dates: List[str], live_root: Path = LIVE_ROOT) -> Dict[str, Any]:
    live_dates = [d for d in dates if (live_root / "training" / d / "l2_training.sqlite3").exists()]
    if not live_dates:
        return {"status": "UNAVAILABLE", "matched_rows": 0, "reason": "no_overlapping_live_sqlite_days"}
    hist = _read_training_rows(dataset_root, live_dates)
    live = _read_training_rows(live_root, live_dates)
    if hist.empty or live.empty:
        return {"status": "UNAVAILABLE", "matched_rows": 0, "reason": "empty_historical_or_live_rows", "overlap_days": live_dates}
    h = _feature_table(hist).add_suffix("_hist")
    l = _feature_table(live).add_suffix("_live")
    merged = h.merge(
        l,
        left_on=["symbol_hist", "sample_bucket_hist"],
        right_on=["symbol_live", "sample_bucket_live"],
        how="inner",
    )
    n = int(len(merged))
    if n == 0:
        return {"status": "UNAVAILABLE", "matched_rows": 0, "reason": "no_exact_5s_bucket_matches", "overlap_days": live_dates}
    metrics: Dict[str, Any] = {}
    severe: List[str] = []
    for c in PARITY_FEATURES:
        m = _parity_metric(merged[f"{c}_hist"], merged[f"{c}_live"], c)
        metrics[c] = m
        if m.get("severe_mismatch"):
            severe.append(c)
    label = _parity_metric(
        merged["ret_smoothed_mid_60_pct_hist"], merged["ret_smoothed_mid_60_pct_live"], "ret_smoothed_mid_60_pct"
    )
    label_bad = bool(label.get("severe_mismatch"))
    frac = len(severe) / max(1, len(PARITY_FEATURES))
    if n < 100:
        status = "INSUFFICIENT_OVERLAP"
    elif label_bad or frac > 0.40:
        status = "FAIL"
    elif frac > 0.20:
        status = "WARN"
    else:
        status = "PASS_WITH_WARNINGS" if severe else "PASS"
    return {
        "status": status, "matched_rows": n, "overlap_days": live_dates,
        "severe_feature_mismatches": severe, "severe_feature_fraction": frac,
        "label_parity": label, "feature_metrics": metrics,
        "interpretation": "Historical model metrics must be discounted or rejected when parity FAILs; this check never tunes model parameters.",
    }


def _auc(y: np.ndarray, p: np.ndarray) -> Optional[float]:
    y = np.asarray(y, dtype=int)
    if len(np.unique(y)) < 2:
        return None
    try:
        return float(roc_auc_score(y, p))
    except Exception:
        return None


def _effective_null_control(dataset_root: Path, dates: List[str], repeats: int = 5) -> Dict[str, Any]:
    """Permutation negative control independent of signal thresholds/abstention.

    A V5 head may legitimately disable itself.  Therefore leakage testing must
    not depend on V5 producing directional trades.  We shuffle TRAIN labels,
    fit the same regularized Logistic heads, and inspect ROC-AUC on the final
    chronological, 60-second non-overlap test day.  A median near 0.5 is desired.
    """
    import services.train_l1_60s_model_v4 as core
    import services.train_l1_60s_model_v4r as v4r
    import services.train_l1_60s_model_v5_challenger as v5
    import services.train_l2_60s_model_v3 as splitbase

    try:
        frame = core._prepare("ALL", dataset_root, HURDLE_BP)
        if frame.empty:
            return {"ok": False, "reason": "empty_dataset"}
        test_day = dates[-1]
        train = frame[frame["trade_date"].astype(str) < str(test_day)].copy()
        test = frame[frame["trade_date"].astype(str) == str(test_day)].copy()
        train = v4r._rotating_thin(train, core.TRAIN_THIN_SECONDS)
        test = splitbase._nonoverlap(test)
        if train.empty or test.empty:
            return {"ok": False, "reason": "empty_train_or_test"}

        real_models = []
        for target in (core.UP_TARGET, core.DOWN_TARGET):
            model = v5._models()["logistic_balanced"]
            core._fit("logistic_balanced", model, train[core.FEATURES], train[target].astype(int).to_numpy())
            real_models.append(model)
        real = {
            "up_auc": _auc(test[core.UP_TARGET].astype(int).to_numpy(), core._positive_probability(real_models[0], test[core.FEATURES])),
            "down_auc": _auc(test[core.DOWN_TARGET].astype(int).to_numpy(), core._positive_probability(real_models[1], test[core.FEATURES])),
        }

        runs: List[Dict[str, Any]] = []
        y_up = train[core.UP_TARGET].astype(int).to_numpy()
        y_dn = train[core.DOWN_TARGET].astype(int).to_numpy()
        for seed in range(repeats):
            rng = np.random.default_rng(7300 + seed)
            up = v5._models()["logistic_balanced"]
            dn = v5._models()["logistic_balanced"]
            core._fit("logistic_balanced", up, train[core.FEATURES], rng.permutation(y_up))
            core._fit("logistic_balanced", dn, train[core.FEATURES], rng.permutation(y_dn))
            runs.append({
                "seed": seed,
                "up_auc": _auc(test[core.UP_TARGET].astype(int).to_numpy(), core._positive_probability(up, test[core.FEATURES])),
                "down_auc": _auc(test[core.DOWN_TARGET].astype(int).to_numpy(), core._positive_probability(dn, test[core.FEATURES])),
            })
        vals = [float(x[k]) for x in runs for k in ("up_auc", "down_auc") if x.get(k) is not None]
        median = float(np.median(vals)) if vals else None
        alarm = bool(median is not None and (median < 0.42 or median > 0.58))
        return {
            "ok": True, "test_day": test_day, "test_rows": int(len(test)), "repeats": repeats,
            "real_label_auc": real, "shuffled_label_runs": runs,
            "shuffled_auc_median": median, "leakage_alarm": alarm,
            "expected": "shuffled-label median ROC-AUC should remain near 0.50; this diagnostic does not select thresholds",
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _sync_cloud(report: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from modules.cloud_bridge import CloudBridge, load_bridge_config
        cfg = load_bridge_config(); bridge = CloudBridge(cfg, timeout=20.0)
        payload = {
            "bridge_id": cfg.bridge_id, "scope": CLOUD_SCOPE,
            "trainer_version": VERSION, "generated_at": report["generated_at"],
            "maturity": "HISTORICAL_REFERENCE_ONLY",
            "protocol": "QMT_TICK_5S_REPLAY__WALK_FORWARD__PARITY__PERMUTATION_NULL",
            "samples_total": int((report.get("dataset") or {}).get("rows") or 0),
            "samples_test_nonoverlap": int((((report.get("aggregates") or {}).get("V5R") or {}).get("logistic_balanced") or {}).get("test_rows") or 0),
            "report": report,
        }
        bridge._request("POST", "ml_training_reports_v1?on_conflict=bridge_id,scope,generated_at", json=payload,
                        headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return {"ok": True, "scope": CLOUD_SCOPE}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--stocks", default=",".join(base.DEFAULT_STOCKS))
    p.add_argument("--end", default="")
    a = p.parse_args()

    now = datetime.now(base.CN_TZ)
    minute = now.hour * 60 + now.minute
    if now.weekday() < 5 and ((570 <= minute < 690) or (780 <= minute < 900)):
        print("[STOP] Historical audit is blocked during market hours to protect QMT live capture.")
        return 3
    stocks = [x.strip().upper() for x in str(a.stocks).split(",") if x.strip()]
    end_d = date.today() if not a.end else datetime.strptime(a.end, "%Y%m%d").date()
    start_d = end_d - timedelta(days=max(14, int(a.days)))
    start, end = start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d")
    stamp = now.strftime("%Y%m%d_%H%M%S")
    run_root = OUT_ROOT / stamp
    dataset_root = run_root / "dataset"
    audit_root = run_root / "audit"
    run_root.mkdir(parents=True, exist_ok=True)

    print(f"AStock QMT historical audit {VERSION}")
    print("NO MODEL PARAMETER CHANGES. Historical results cannot auto-promote/deploy.")
    try:
        from xtquant import xtdata
    except Exception as exc:
        print(f"[FAIL] xtquant import: {exc}")
        return 2

    report: Dict[str, Any] = {
        "version": VERSION, "generated_at": datetime.now(base.CN_TZ).isoformat(timespec="seconds"),
        "start": start, "end": end, "stocks": stocks, "benchmarks": base.BENCHMARKS,
        "orders_placed": False, "live_runtime_stopped": False,
        "qualification": "HISTORICAL_REFERENCE_ONLY_NOT_PRISTINE_OOS",
        "architecture_lookahead_risk": True,
        "model_parameters_changed_by_v2": False,
        "eligible_for_champion_promotion": False, "eligible_for_live_deployment": False,
        "audit_repairs": ["variant_labeled_logs", "saved_report_invariants", "historical_live_parity", "threshold_independent_permutation_null"],
    }
    v3_mod = original_expand = None
    try:
        report["dataset"] = base._build_dataset(xtdata, stocks, start, end, dataset_root)
        dates = list(report["dataset"]["trade_dates"])
        report["historical_live_parity"] = _historical_live_parity(dataset_root, dates)

        start_idx = 5 if len(dates) >= 6 else len(dates) - 1
        test_indices = list(range(start_idx, len(dates)))
        if len(test_indices) > 5:
            test_indices = test_indices[-5:]

        v3_mod, original_expand = base._session_safe_expand_patch()
        folds: List[Dict[str, Any]] = []
        for i, idx in enumerate(test_indices, 1):
            folds.append(_run_fold(dataset_root, audit_root, dates[:idx + 1], i))
        report["folds"] = folds
        report["aggregates"] = {"V4R": {}, "V5R": {}}
        for variant in ("V4R", "V5R"):
            for family in ("logistic_balanced", "hist_gradient_boosting"):
                report["aggregates"][variant][family] = base._aggregate(folds, variant, family)
        report["null_control"] = _effective_null_control(dataset_root, dates)

        v4 = report["aggregates"]["V4R"]["logistic_balanced"]
        v5 = report["aggregates"]["V5R"]["logistic_balanced"]
        report["diagnosis"] = {
            "v4r_positive_historical_alpha": bool((v4.get("avg_net_edge_bp") or -1e9) > 0 and (v4.get("directional_accuracy_pct") or 0) >= 55),
            "v5r_all_watch": int(v5.get("directional_predictions") or 0) == 0,
            "v5r_interpretation": "Frozen robust gate found no validation-stable head. Do not loosen from these historical results; wait for more prospective days or build a new separately-versioned challenger.",
            "parity_required_before_historical_model_tuning": True,
        }
    except Exception as exc:
        report["fatal_error"] = f"{type(exc).__name__}: {exc}"
        print(f"[FAIL] {report['fatal_error']}")
    finally:
        if v3_mod is not None and original_expand is not None:
            v3_mod._expand = original_expand

    report["cloud_sync"] = _sync_cloud(report)
    path = run_root / "walkforward_report_v2.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = OUT_ROOT / "latest.json"; latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[REPORT] {path}")
    if report.get("fatal_error"):
        return 1
    v4 = report.get("aggregates", {}).get("V4R", {}).get("logistic_balanced", {})
    v5 = report.get("aggregates", {}).get("V5R", {}).get("logistic_balanced", {})
    parity = report.get("historical_live_parity") or {}
    null = report.get("null_control") or {}
    print("[RESULT] frozen historical audit")
    print(f"  V4R accuracy={v4.get('directional_accuracy_pct')}% n={v4.get('directional_predictions')} coverage={v4.get('directional_coverage_pct')}% net={v4.get('avg_net_edge_bp')}bp")
    print(f"  V5R accuracy={v5.get('directional_accuracy_pct')}% n={v5.get('directional_predictions')} coverage={v5.get('directional_coverage_pct')}% net={v5.get('avg_net_edge_bp')}bp")
    print(f"  parity={parity.get('status')} matched={parity.get('matched_rows')} severe={parity.get('severe_feature_mismatches')}")
    print(f"  shuffled-null median AUC={null.get('shuffled_auc_median')} leakage_alarm={null.get('leakage_alarm')}")
    print("[IMPORTANT] V2 did not tune model parameters. Sep-07+ prospective OOS remains mandatory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
