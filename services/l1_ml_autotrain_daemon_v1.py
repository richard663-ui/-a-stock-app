# -*- coding: utf-8 -*-
"""Automatic L1/Tick 60s research trainer.

Runs at lunch and after close, trains 301236.SZ and the pooled eight-stock model,
and syncs reports/status to the existing Supabase research tables. No live model
is ever auto-deployed.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple

from modules.cloud_bridge import CloudBridge, load_bridge_config

AUTO_TRAINER_VERSION = "l1-ml-autotrain-v1-20260904"
DATA_ROOT = Path.home() / "AStockData"
MODEL_DIR = DATA_ROOT / "models" / "l1_60s"
STATE_PATH = MODEL_DIR / "autotrain_state.json"
TRAINER = Path(__file__).with_name("train_l1_60s_model_v1.py")
CHECK_SECONDS = 60
COUNT_REFRESH_SECONDS = 300
PRIORITY_SCOPE = "301236.SZ"
PRIORITY_MIN = 200
POOLED_MIN = 600


def _load_state() -> Dict[str, Any]:
    try:
        obj = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _slot(now: datetime) -> str:
    if now.weekday() >= 5:
        return ""
    minute = now.hour * 60 + now.minute
    if 11 * 60 + 35 <= minute < 12 * 60 + 30:
        return "AM"
    if 15 * 60 + 10 <= minute < 18 * 60:
        return "PM"
    return ""


def _market_open(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= minute < 11 * 60 + 30) or (13 * 60 <= minute < 15 * 60)


def _eligible_count(scope: str) -> int:
    total = 0
    for path in sorted((DATA_ROOT / "training").glob("*/l2_training.sqlite3")):
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
            try:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_samples_v2'"
                ).fetchone()
                if not exists:
                    continue
                where = (
                    "valid=1 AND labeled_at IS NOT NULL "
                    "AND label_smoothed_mid_60 IN (-1,0,1) "
                    "AND upper(coalesce(session,'')) <> 'OPEN_AUCTION'"
                )
                args: Tuple[Any, ...] = ()
                if scope.upper() != "ALL":
                    where += " AND upper(symbol)=?"
                    args = (scope.upper(),)
                # One observation per symbol/5s bucket even if an updater changed
                # recorder versions during the same session.
                row = conn.execute(
                    f"SELECT count(*) FROM (SELECT symbol,sample_bucket FROM training_samples_v2 WHERE {where} GROUP BY symbol,sample_bucket)",
                    args,
                ).fetchone()
                total += int(row[0] if row else 0)
            finally:
                conn.close()
        except Exception:
            continue
    return total


def _counts() -> Dict[str, int]:
    return {
        "priority_samples": _eligible_count(PRIORITY_SCOPE),
        "pooled_samples": _eligible_count("ALL"),
    }


def _run_one(scope: str, minimum: int, log_handle) -> int:
    cmd = [
        sys.executable, str(TRAINER), "--symbol", scope,
        "--min-samples", str(minimum), "--hurdle-bp", "2.0",
    ]
    log_handle.write("\n$ " + " ".join(cmd) + "\n")
    log_handle.flush()
    p = subprocess.run(cmd, stdout=log_handle, stderr=subprocess.STDOUT, text=True)
    return int(p.returncode)


def _cloud() -> Tuple[CloudBridge, str]:
    cfg = load_bridge_config()
    return CloudBridge(cfg, timeout=8.0), cfg.bridge_id


def _report_path(scope: str) -> Path:
    return MODEL_DIR / f"{scope.upper().replace('.', '_')}_training_report_latest.json"


def _sync_report(scope: str, log_handle) -> bool:
    path = _report_path(scope)
    if not path.exists():
        log_handle.write(f"[CLOUD] report not found: {path}\n")
        return False
    try:
        report: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        bridge, bridge_id = _cloud()
        payload = {
            "bridge_id": bridge_id,
            "scope": str(report.get("scope") or scope.upper()),
            "trainer_version": str(report.get("trainer_version") or "unknown"),
            "generated_at": report.get("generated_at"),
            "maturity": report.get("maturity"),
            "protocol": report.get("protocol"),
            "samples_total": int(report.get("samples_total") or 0),
            "samples_test_nonoverlap": int(report.get("samples_test_nonoverlap") or 0),
            "report": report,
        }
        bridge._request(
            "POST",
            "ml_training_reports_v1?on_conflict=bridge_id,scope,generated_at",
            json=payload,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        log_handle.write(f"[CLOUD] synced L1 report scope={payload['scope']} nonoverlap={payload['samples_test_nonoverlap']}\n")
        return True
    except Exception as exc:
        log_handle.write(f"[CLOUD] sync failed; local report preserved: {exc}\n")
        return False


def _publish(state: Dict[str, Any], counts: Dict[str, int], slot: str) -> None:
    try:
        bridge, bridge_id = _cloud()
        now_text = datetime.now().astimezone().isoformat(timespec="seconds")
        payload = {
            "bridge_id": bridge_id,
            "daemon_version": AUTO_TRAINER_VERSION,
            "state": str(state.get("run_state") or "IDLE"),
            "slot": slot or None,
            "last_heartbeat_at": now_text,
            "last_attempt_at": state.get("last_attempt_at"),
            "priority_samples": int(counts.get("priority_samples") or 0),
            "pooled_samples": int(counts.get("pooled_samples") or 0),
            "priority_required": PRIORITY_MIN,
            "pooled_required": POOLED_MIN,
            "priority_result": state.get("priority_result"),
            "pooled_result": state.get("pooled_result"),
            "message": state.get("message"),
            "updated_at": now_text,
        }
        bridge._request(
            "POST", "ml_training_status_v1?on_conflict=bridge_id", json=payload,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
    except Exception as exc:
        print(f"[WARN] L1 ML heartbeat sync failed: {exc}")


def _result(rc: int, synced: bool) -> str:
    if rc == 0 and synced:
        return "TRAINED_SYNCED"
    if rc == 0:
        return "TRAINED_LOCAL_SYNC_FAILED"
    return f"TRAIN_FAILED_RC_{rc}"


def _train(now: datetime, slot: str, counts: Dict[str, int], state: Dict[str, Any]) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    log_path = MODEL_DIR / f"autotrain_{now.strftime('%Y-%m-%d')}_{slot}.log"
    attempt = datetime.now().astimezone().isoformat(timespec="seconds")
    state.update({
        "run_state": "TRAINING", "last_attempt_at": attempt,
        "priority_result": "PENDING", "pooled_result": "PENDING",
        "message": "L1/Tick automatic training attempt started.",
    })
    _save_state(state); _publish(state, counts, slot)

    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n=== L1 auto train {attempt} ===\n")
        log.write(f"priority={counts['priority_samples']}/{PRIORITY_MIN} pooled={counts['pooled_samples']}/{POOLED_MIN}\n")
        if counts["priority_samples"] < PRIORITY_MIN:
            state["priority_result"] = "SKIPPED_NOT_ENOUGH_DATA"
        else:
            rc = _run_one(PRIORITY_SCOPE, PRIORITY_MIN, log)
            state["priority_result"] = _result(rc, _sync_report(PRIORITY_SCOPE, log) if rc == 0 else False)
        if counts["pooled_samples"] < POOLED_MIN:
            state["pooled_result"] = "SKIPPED_NOT_ENOUGH_DATA"
        else:
            rc = _run_one("ALL", POOLED_MIN, log)
            state["pooled_result"] = _result(rc, _sync_report("ALL", log) if rc == 0 else False)

        results = {state["priority_result"], state["pooled_result"]}
        if any(str(x).startswith("TRAINED") for x in results):
            state["run_state"] = "TRAINED"
            state["message"] = "At least one L1/Tick research model trained; no live deployment occurred."
        elif results == {"SKIPPED_NOT_ENOUGH_DATA"}:
            state["run_state"] = "SKIPPED_NOT_ENOUGH_DATA"
            state["message"] = "L1/Tick trainer is healthy; valid labeled sample minimum has not been reached."
        else:
            state["run_state"] = "ERROR"
            state["message"] = "L1 training was attempted but at least one scope failed; inspect the local log."
        log.write(f"priority_result={state['priority_result']} pooled_result={state['pooled_result']} state={state['run_state']}\n")

    state["last_finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _save_state(state); _publish(state, counts, slot)


def main() -> None:
    print("AStock L1/Tick 60s ML auto-trainer started")
    print(f"Daemon: {AUTO_TRAINER_VERSION}")
    print("Schedules: 11:35 lunch + 15:10 after close. Research only; no auto deployment.")
    state = _load_state()
    previous = str(state.get("daemon_version") or "")
    force_version_attempt = previous != AUTO_TRAINER_VERSION
    state["daemon_version"] = AUTO_TRAINER_VERSION
    state.setdefault("run_state", "IDLE")
    state.setdefault("message", "L1/Tick auto-trainer running; waiting for the next training slot.")
    counts = _counts(); last_refresh = time.time()
    _save_state(state); _publish(state, counts, _slot(datetime.now()))

    while True:
        now = datetime.now(); slot = _slot(now); now_ts = time.time()
        if (not _market_open(now)) and now_ts - last_refresh >= COUNT_REFRESH_SECONDS:
            counts = _counts(); last_refresh = now_ts
        if slot:
            key = f"{now.strftime('%Y-%m-%d')}:{slot}"
            if state.get("last_slot") != key or force_version_attempt:
                try:
                    counts = _counts(); last_refresh = time.time()
                    _train(now, slot, counts, state)
                except Exception as exc:
                    state["run_state"] = "ERROR"
                    state["last_attempt_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                    state["message"] = f"L1 auto-train exception: {exc}"
                    print(f"[WARN] L1 auto-train failed: {exc}")
                finally:
                    state["last_slot"] = key
                    state["daemon_version"] = AUTO_TRAINER_VERSION
                    force_version_attempt = False
                    _save_state(state); _publish(state, counts, slot)
        _publish(state, counts, slot)
        time.sleep(CHECK_SECONDS)


if __name__ == "__main__":
    main()
