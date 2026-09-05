# -*- coding: utf-8 -*-
"""Prospective L1 V6 60s shadow runner.

Research-only. This process is isolated from the recorder and production phone.
It reads the recorder's local SQLite rows, scores one non-overlapping observation
per symbol/minute with a model frozen before the trading session, and settles
+60s performance from OBSERVED bid1/ask1 rows in the existing 5-second recorder.

Governance:
- no auto deployment;
- V6 thresholds stay frozen; raw UP/DOWN probabilities are stored even on WATCH;
- one model is frozen for the entire trading day, so an 11:35 same-day retrain
  cannot leak into afternoon prospective results;
- if no V6 model exists, bootstrap training is allowed only while market closed;
- OPEN_AUCTION rows are excluded from feature-delta construction to match the
  V6 trainer's continuous-auction data path;
- final session minute is not scored because a full +55..+65s quote window does
  not exist after the market closes.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd

from modules.cloud_bridge import CloudBridge, load_bridge_config
import services.train_l1_60s_model_v3 as v3
import services.train_l1_60s_model_v4 as core
import services.train_l1_60s_model_v6_exec_aligned as v6

RUNNER_VERSION = "l1-v6-shadow-runner-v1-observed-bid-20260905"
DATA_ROOT = Path.home() / "AStockData"
TRAINING_ROOT = DATA_ROOT / "training"
MODEL_DIR = DATA_ROOT / "models" / "l1_60s" / "v6_exec_aligned"
REPORT_PATH = MODEL_DIR / "ALL_training_report_latest.json"
LOCAL_STATE_DIR = Path.home() / ".a_stock_qmt"
FREEZE_STATE_PATH = LOCAL_STATE_DIR / "v6_shadow_freeze.json"
PENDING_DB_PATH = LOCAL_STATE_DIR / "v6_shadow_pending.sqlite3"
TRAINER_PATH = Path(__file__).with_name("train_l1_60s_model_v6_exec_aligned.py")
MODEL_FAMILY = "logistic_balanced"
HURDLE_BP = 2.0
LOOKBACK_SECONDS = 300.0
SETTLE_AFTER_SECONDS = 67.0
FUTURE_WINDOW_LO = 55.0
FUTURE_WINDOW_HI = 65.0
POINT_TOLERANCE_SECONDS = 4.0
HEARTBEAT_SECONDS = 15.0
POLL_SECONDS = 1.0
STABLE_SYMBOLS = tuple(v6.STABLE_SYMBOLS)


def _now() -> datetime:
    return datetime.now().astimezone()


def _market_open(now: Optional[datetime] = None) -> bool:
    d = now or _now()
    if d.weekday() >= 5:
        return False
    m = d.hour * 60 + d.minute
    return (570 <= m < 690) or (780 <= m < 900)


def _phase(minute: float) -> str:
    if 570 <= minute < 630:
        return "OPEN_CORE_0930_1030"
    if 630 <= minute < 690:
        return "AM_LATE_1030_1130"
    if 780 <= minute < 900:
        return "PM_1300_1500"
    return "OUTSIDE_CONTINUOUS"


def _daily_db(day: str) -> Path:
    return TRAINING_ROOT / day / "l2_training.sqlite3"


def _connect_pending() -> sqlite3.Connection:
    LOCAL_STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(PENDING_DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending (
            sample_key TEXT NOT NULL,
            model_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            generated_ts REAL NOT NULL,
            payload_json TEXT NOT NULL,
            settled INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(sample_key, model_version)
        )
    """)
    conn.commit()
    return conn


def _read_rows(path: Path, lo_ts: float, hi_ts: Optional[float] = None,
               symbol: Optional[str] = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    try:
        where = ["generated_ts>=?"]
        args: List[Any] = [float(lo_ts)]
        if hi_ts is not None:
            where.append("generated_ts<=?")
            args.append(float(hi_ts))
        if symbol:
            where.append("symbol=?")
            args.append(str(symbol).upper())
        out = pd.read_sql_query(
            "SELECT * FROM training_samples_v2 WHERE " + " AND ".join(where) + " ORDER BY generated_ts",
            conn, params=args,
        )
    finally:
        conn.close()
    if out.empty:
        return out
    if "sample_bucket" in out.columns:
        out = out.sort_values("generated_ts").drop_duplicates(["symbol", "sample_bucket"], keep="last")
    return out.sort_values(["symbol", "generated_ts"]).reset_index(drop=True)


def _feature_frame(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw.copy()
    work = raw.copy()
    # Trainer V3 removes OPEN_AUCTION before computing volume/spread deltas. Do
    # the same here or the 09:30 first continuous sample would see a false jump.
    if "session" in work.columns:
        work = work[work["session"].astype(str).str.upper() != "OPEN_AUCTION"].copy()
    if work.empty:
        return work
    frame = core._add_regime_features(v3._expand(work))
    sym = frame.get("symbol", pd.Series("", index=frame.index)).astype(str).str.upper()
    for raw_symbol, feature in zip(v6.STABLE_SYMBOLS, v6.SYMBOL_FEATURES):
        frame[feature] = (sym == raw_symbol).astype(float)
    for col in v6.FEATURES:
        if col not in frame.columns:
            frame[col] = np.nan
        frame[col] = pd.to_numeric(frame[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return frame


def _parse_dt(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (d.astimezone() if d.tzinfo is not None else d.astimezone())
    except Exception:
        return None


def _load_freeze_state() -> Dict[str, Any]:
    try:
        return json.loads(FREEZE_STATE_PATH.read_text(encoding="utf-8")) if FREEZE_STATE_PATH.exists() else {}
    except Exception:
        return {}


def _save_freeze_state(obj: Dict[str, Any]) -> None:
    LOCAL_STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FREEZE_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(FREEZE_STATE_PATH)


def _report_bundle(report_path: Path = REPORT_PATH) -> Tuple[Dict[str, Any], Dict[str, Any], Path]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    item = dict((report.get("models") or {}).get(MODEL_FAMILY) or {})
    model_path = Path(str(item.get("model_path") or "")).expanduser()
    if not model_path.exists():
        raise FileNotFoundError(f"V6 model artifact missing: {model_path}")
    return report, joblib.load(model_path), model_path


def _model_version(report: Dict[str, Any], model_path: Path) -> str:
    base = str(report.get("trainer_version") or v6.TRAINER_VERSION)
    generated = str(report.get("generated_at") or model_path.stem)
    compact = generated.replace(":", "").replace("+", "_").replace(" ", "T")
    return f"{base}@{compact}"


def _freeze_model_for_day(day: str) -> Tuple[Dict[str, Any], Dict[str, Any], Path, str]:
    state = _load_freeze_state()
    saved = dict((state.get("days") or {}).get(day) or {})
    if saved:
        path = Path(str(saved.get("model_path") or "")).expanduser()
        report_snapshot = dict(saved.get("report") or {})
        if path.exists() and report_snapshot:
            bundle = joblib.load(path)
            version = str(saved.get("model_version") or _model_version(report_snapshot, path))
            return report_snapshot, bundle, path, version

    report, bundle, path = _report_bundle()
    generated = _parse_dt(report.get("generated_at"))
    today = datetime.strptime(day, "%Y-%m-%d").date()
    cutoff = datetime.combine(today, dtime(9, 25)).astimezone()
    # On a real trading day, a same-day post-09:25 model may contain that day's
    # labels and is not allowed as a fresh shadow model. Weekend bootstrap is
    # naturally older than the next trading day's cutoff.
    if generated is not None and generated > cutoff:
        raise RuntimeError(
            f"latest V6 model generated after {cutoff.isoformat()}; refusing same-day leakage without saved pre-open freeze"
        )
    version = _model_version(report, path)
    days = dict(state.get("days") or {})
    days[day] = {
        "model_path": str(path), "model_version": version,
        "frozen_at": _now().isoformat(timespec="seconds"),
        "report": {
            "trainer_version": report.get("trainer_version"),
            "generated_at": report.get("generated_at"), "scope": report.get("scope", "ALL"),
        },
    }
    for old_day in sorted(days)[:-20]:
        days.pop(old_day, None)
    _save_freeze_state({"runner_version": RUNNER_VERSION, "days": days})
    return report, bundle, path, version


def _bootstrap_v6_if_missing() -> Tuple[bool, str]:
    if REPORT_PATH.exists():
        return True, "existing_v6_report"
    if _market_open():
        return False, "market_open_bootstrap_forbidden"
    if not TRAINER_PATH.exists():
        return False, f"trainer_missing:{TRAINER_PATH}"
    cmd = [sys.executable, str(TRAINER_PATH), "--symbol", "ALL", "--min-samples", "600", "--hurdle-bp", str(HURDLE_BP)]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    tail = " | ".join(x.strip() for x in (p.stdout or "").splitlines()[-12:] if x.strip())[-1000:]
    return bool(p.returncode == 0 and REPORT_PATH.exists()), f"bootstrap_rc={p.returncode}:{tail}"


def _cloud() -> Tuple[CloudBridge, str]:
    cfg = load_bridge_config()
    return CloudBridge(cfg, timeout=5.0), cfg.bridge_id


def _sync_report(bridge: CloudBridge, bridge_id: str, report: Dict[str, Any]) -> None:
    bridge._request(
        "POST", "ml_training_reports_v1?on_conflict=bridge_id,scope,generated_at",
        json={
            "bridge_id": bridge_id, "scope": "ALL::V6_CHALLENGER",
            "trainer_version": str(report.get("trainer_version") or v6.TRAINER_VERSION),
            "generated_at": report.get("generated_at"), "maturity": report.get("maturity"),
            "protocol": report.get("protocol"), "samples_total": int(report.get("samples_total") or 0),
            "samples_test_nonoverlap": int(report.get("samples_test_nonoverlap") or 0),
            "report": {**report, "cloud_scope": "ALL::V6_CHALLENGER"},
        },
        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )


def _upsert_shadow(bridge: CloudBridge, payload: Dict[str, Any]) -> None:
    bridge._request(
        "POST", "ml_shadow_samples_v1?on_conflict=bridge_id,sample_key,model_version",
        json=payload, headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )


def _heartbeat(bridge: CloudBridge, bridge_id: str, *, state: str, model_version: Optional[str],
               model_generated_at: Optional[str], last_prediction_at: Optional[str],
               predictions_today: int, settled_today: int, message: str, extra: Dict[str, Any]) -> None:
    now = _now().isoformat(timespec="seconds")
    bridge._request(
        "POST", "ml_shadow_status_v1?on_conflict=bridge_id",
        json={
            "bridge_id": bridge_id, "runner_version": RUNNER_VERSION, "state": state,
            "model_version": model_version, "model_scope": "ALL", "model_family": MODEL_FAMILY,
            "model_generated_at": model_generated_at, "last_heartbeat_at": now,
            "last_prediction_at": last_prediction_at, "predictions_today": int(predictions_today),
            "settled_today": int(settled_today), "message": message, "status": extra, "updated_at": now,
        },
        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )


def _candidate_rows(day_db: Path, now_ts: float) -> pd.DataFrame:
    raw = _read_rows(day_db, now_ts - LOOKBACK_SECONDS, now_ts + 2.0)
    frame = _feature_frame(raw)
    if frame.empty:
        return frame
    frame["minute_bucket"] = (pd.to_numeric(frame["generated_ts"], errors="coerce") // 60).astype("Int64")
    frame = frame[frame["symbol"].astype(str).str.upper().isin(STABLE_SYMBOLS)].copy()
    minute = pd.to_numeric(frame.get("minute_of_day"), errors="coerce")
    # Exclude 11:29 and 14:59: those minute-start predictions cannot always
    # obtain a complete +55..+65s observed quote window before session close.
    frame = frame[((minute >= 570) & (minute < 689)) | ((minute >= 780) & (minute < 899))].copy()
    if frame.empty:
        return frame
    return frame.sort_values("generated_ts").groupby("symbol", as_index=False, sort=False).tail(1)


def _score_row(row: pd.Series, bundle: Dict[str, Any], model_version: str,
               bridge_id: str) -> Dict[str, Any]:
    X = pd.DataFrame([{c: row.get(c, np.nan) for c in v6.FEATURES}], columns=v6.FEATURES)
    up_prob = float(core._positive_probability(bundle["up_model"], X)[0])
    down_prob = float(core._positive_probability(bundle["down_model"], X)[0])
    up_t = float(bundle.get("up_threshold") or 0.999)
    down_t = float(bundle.get("down_threshold") or 0.999)
    pred = int(core._combine(np.asarray([up_prob]), np.asarray([down_prob]), up_t, down_t)[0])
    direction = "UP" if pred == 1 else "DOWN" if pred == -1 else "WATCH"
    ts = float(row["generated_ts"])
    minute = float(row.get("minute_of_day") or 0.0)
    symbol = str(row.get("symbol") or "").upper()
    generated_at = _parse_dt(row.get("generated_at")) or datetime.fromtimestamp(ts).astimezone()
    return {
        "bridge_id": bridge_id, "sample_key": f"{symbol}:{int(ts // 60)}", "symbol": symbol,
        "generated_at": generated_at.isoformat(timespec="milliseconds"), "generated_ts": ts,
        "model_version": model_version, "model_scope": "ALL", "model_family": MODEL_FAMILY,
        "up_prob": up_prob, "down_prob": down_prob, "up_threshold": up_t, "down_threshold": down_t,
        "direction": direction, "phase": _phase(minute),
        "entry_bid": float(row.get("bid1")) if pd.notna(row.get("bid1")) else None,
        "entry_ask": float(row.get("ask1")) if pd.notna(row.get("ask1")) else None,
        "entry_mid": float(row.get("mid_price")) if pd.notna(row.get("mid_price")) else None,
        "status": "PENDING",
        "diagnostic": {
            "runner_version": RUNNER_VERSION, "prospective": True, "same_day_model_switch": False,
            "settlement_policy": "observed 5s bid1/ask1 rows; mean bid1 over +55..+65s; nearest quote to +60s",
        },
        "updated_at": _now().isoformat(timespec="seconds"),
    }


def _settlement_from_rows(payload: Dict[str, Any], future: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if future.empty:
        return None
    t0 = float(payload["generated_ts"])
    work = future.copy()
    for col in ("generated_ts", "bid1", "ask1", "mid_price"):
        work[col] = pd.to_numeric(work.get(col), errors="coerce")
    window = work[(work["generated_ts"] >= t0 + FUTURE_WINDOW_LO) &
                  (work["generated_ts"] <= t0 + FUTURE_WINDOW_HI)].copy()
    valid_bids = window.loc[window["bid1"] > 0, "bid1"].dropna()
    if len(valid_bids) < 2:
        return None
    smooth_bid = float(valid_bids.mean())
    valid_asks = window.loc[window["ask1"] > 0, "ask1"].dropna()
    valid_mids = window.loc[window["mid_price"] > 0, "mid_price"].dropna()
    smooth_ask = float(valid_asks.mean()) if len(valid_asks) else None
    smooth_mid = float(valid_mids.mean()) if len(valid_mids) else None

    point = work.copy()
    point["gap"] = (point["generated_ts"] - (t0 + 60.0)).abs()
    point = point[point["gap"] <= POINT_TOLERANCE_SECONDS].sort_values("gap")
    future_bid_60 = None
    if not point.empty and pd.notna(point.iloc[0].get("bid1")) and float(point.iloc[0].get("bid1") or 0.0) > 0:
        future_bid_60 = float(point.iloc[0]["bid1"])

    entry_ask = float(payload.get("entry_ask") or 0.0)
    entry_bid = float(payload.get("entry_bid") or 0.0)
    if entry_ask <= 0 or entry_bid <= 0:
        return None
    up_ret = (smooth_bid / entry_ask - 1.0) * 100.0
    down_ret = (smooth_bid / entry_bid - 1.0) * 100.0
    hurdle_pct = HURDLE_BP / 100.0
    up_actionable = bool(up_ret > hurdle_pct)
    down_actionable = bool(down_ret < -hurdle_pct)
    direction = str(payload.get("direction") or "WATCH")
    if direction == "UP":
        gross, correct = up_ret * 100.0, up_actionable
    elif direction == "DOWN":
        gross, correct = -down_ret * 100.0, down_actionable
    else:
        gross, correct = None, None
    return {
        **payload, "status": "SETTLED", "future_bid_60": future_bid_60,
        "future_bid_smoothed_60": smooth_bid, "future_ask_60": smooth_ask, "future_mid_60": smooth_mid,
        "up_exec_return_pct": up_ret, "down_hold_return_pct": down_ret,
        "up_actionable": up_actionable, "down_actionable": down_actionable, "correct": correct,
        "gross_edge_bp": gross, "net_edge_bp": (gross - HURDLE_BP) if gross is not None else None,
        "settled_at": _now().isoformat(timespec="seconds"),
        "diagnostic": {
            **dict(payload.get("diagnostic") or {}), "future_window_rows": int(len(window)),
            "future_valid_bid_rows": int(len(valid_bids)), "execution_hurdle_bp": HURDLE_BP,
            "settlement_is_observed_bid_not_proxy": True,
        },
        "updated_at": _now().isoformat(timespec="seconds"),
    }


def _store_pending(conn: sqlite3.Connection, payload: Dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO pending(sample_key,model_version,symbol,generated_ts,payload_json,settled) VALUES(?,?,?,?,?,0)",
        (payload["sample_key"], payload["model_version"], payload["symbol"], payload["generated_ts"],
         json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)),
    )
    conn.commit()


def _pending_rows(conn: sqlite3.Connection, now_ts: float) -> List[Tuple[str, str, str, float, Dict[str, Any]]]:
    rows = conn.execute(
        "SELECT sample_key,model_version,symbol,generated_ts,payload_json FROM pending WHERE settled=0 AND generated_ts<=? ORDER BY generated_ts",
        (float(now_ts - SETTLE_AFTER_SECONDS),),
    ).fetchall()
    out: List[Tuple[str, str, str, float, Dict[str, Any]]] = []
    for key, version, symbol, ts, raw in rows:
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}
        out.append((str(key), str(version), str(symbol), float(ts), payload))
    return out


def _mark_settled(conn: sqlite3.Connection, sample_key: str, model_version: str) -> None:
    conn.execute("UPDATE pending SET settled=1 WHERE sample_key=? AND model_version=?", (sample_key, model_version))
    conn.commit()


def _count_today(conn: sqlite3.Connection, day_start_ts: float) -> Tuple[int, int]:
    total = int(conn.execute("SELECT COUNT(*) FROM pending WHERE generated_ts>=?", (day_start_ts,)).fetchone()[0])
    settled = int(conn.execute("SELECT COUNT(*) FROM pending WHERE generated_ts>=? AND settled=1", (day_start_ts,)).fetchone()[0])
    return total, settled


def main() -> None:
    print("AStock V6 prospective 60s shadow runner started")
    print(f"Runner: {RUNNER_VERSION}")
    print("Observed future bid settlement; recorder/production processes are not patched.")
    print("One score per symbol/minute; daily model freeze blocks same-day retrain leakage.")

    ok, bootstrap_message = _bootstrap_v6_if_missing()
    bridge, bridge_id = _cloud()
    if ok and REPORT_PATH.exists():
        try:
            _sync_report(bridge, bridge_id, json.loads(REPORT_PATH.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"[WARN] initial V6 report sync: {exc}")

    pending_conn = _connect_pending()
    model_report: Dict[str, Any] = {}
    model_bundle: Optional[Dict[str, Any]] = None
    model_path: Optional[Path] = None
    model_version: Optional[str] = None
    frozen_day = ""
    last_heartbeat = 0.0
    last_prediction_at: Optional[str] = None
    seen_keys: set[Tuple[str, str]] = set()

    try:
        while True:
            now = _now()
            now_ts = time.time()
            day = now.strftime("%Y-%m-%d")
            day_db = _daily_db(day)
            state = "RUNNING" if _market_open(now) else "MARKET_CLOSED"
            message = bootstrap_message

            if frozen_day != day:
                model_report, model_bundle, model_path, model_version = {}, None, None, None
                frozen_day = day
                try:
                    model_report, model_bundle, model_path, model_version = _freeze_model_for_day(day)
                    message = f"daily_model_frozen:{model_path.name}"
                except Exception as exc:
                    message = f"WAITING_MODEL:{type(exc).__name__}:{exc}"
                    state = "WAITING_MODEL" if _market_open(now) else "MARKET_CLOSED_WAITING_MODEL"

            if _market_open(now) and model_bundle is not None and model_version:
                try:
                    for _, row in _candidate_rows(day_db, now_ts).iterrows():
                        key = (f"{str(row['symbol']).upper()}:{int(float(row['generated_ts']) // 60)}", model_version)
                        if key in seen_keys:
                            continue
                        payload = _score_row(row, model_bundle, model_version, bridge_id)
                        _store_pending(pending_conn, payload)
                        _upsert_shadow(bridge, payload)
                        seen_keys.add(key)
                        last_prediction_at = str(payload["generated_at"])
                except Exception as exc:
                    message = f"score_error:{type(exc).__name__}:{exc}"
                    print(f"[WARN] {message}")

            for sample_key, version, symbol, generated_ts, payload in _pending_rows(pending_conn, now_ts):
                sample_day = datetime.fromtimestamp(generated_ts).astimezone().strftime("%Y-%m-%d")
                future = _read_rows(_daily_db(sample_day), generated_ts + FUTURE_WINDOW_LO - 1.0,
                                    generated_ts + FUTURE_WINDOW_HI + 1.0, symbol=symbol)
                settled = _settlement_from_rows(payload, future)
                if settled is None:
                    if now_ts - generated_ts > 300:
                        failed = {
                            **payload, "status": "INVALID", "settled_at": _now().isoformat(timespec="seconds"),
                            "diagnostic": {**dict(payload.get("diagnostic") or {}),
                                           "invalid_reason": "insufficient_future_bid_rows"},
                            "updated_at": _now().isoformat(timespec="seconds"),
                        }
                        try:
                            _upsert_shadow(bridge, failed)
                        finally:
                            _mark_settled(pending_conn, sample_key, version)
                    continue
                try:
                    _upsert_shadow(bridge, settled)
                    _mark_settled(pending_conn, sample_key, version)
                except Exception as exc:
                    print(f"[WARN] settlement sync retained for retry: {exc}")

            if now_ts - last_heartbeat >= HEARTBEAT_SECONDS:
                start = datetime.combine(now.date(), dtime.min).astimezone().timestamp()
                predictions_today, settled_today = _count_today(pending_conn, start)
                try:
                    _heartbeat(
                        bridge, bridge_id, state=state, model_version=model_version,
                        model_generated_at=str(model_report.get("generated_at") or "") or None,
                        last_prediction_at=last_prediction_at, predictions_today=predictions_today,
                        settled_today=settled_today, message=message,
                        extra={
                            "market_open": _market_open(now), "day": day,
                            "model_path": str(model_path) if model_path else None, "frozen_day": frozen_day,
                            "stable_symbols": list(STABLE_SYMBOLS), "settlement": "OBSERVED_BID1_MEAN_55_65S",
                            "score_frequency": "ONE_PER_SYMBOL_PER_60S_BUCKET", "auto_deployed": False,
                        },
                    )
                except Exception as exc:
                    print(f"[WARN] shadow heartbeat sync: {exc}")
                last_heartbeat = now_ts
            time.sleep(POLL_SECONDS)
    finally:
        pending_conn.close()


if __name__ == "__main__":
    main()
