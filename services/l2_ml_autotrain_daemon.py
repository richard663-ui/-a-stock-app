# -*- coding: utf-8 -*-
"""Lightweight automatic L2 ML trainer scheduler.

Runs only during the lunch break / after close so it does not compete with live
recorders. It trains research models, writes reports locally, syncs completed
reports to Supabase, and never deploys a model to the phone/live path.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from modules.cloud_bridge import CloudBridge, load_bridge_config

DATA_ROOT = Path.home() / "AStockData"
MODEL_DIR = DATA_ROOT / "models" / "l2_60s"
STATE_PATH = MODEL_DIR / "autotrain_state.json"
TRAINER = Path(__file__).with_name("train_l2_60s_model_v3.py")
CHECK_SECONDS = 60


def _load_state() -> Dict[str, str]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, str]) -> None:
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


def _run_one(scope: str, minimum: int, log_handle) -> int:
    cmd = [sys.executable, str(TRAINER), "--symbol", scope, "--min-samples", str(minimum)]
    log_handle.write("\n$ " + " ".join(cmd) + "\n")
    log_handle.flush()
    p = subprocess.run(cmd, stdout=log_handle, stderr=subprocess.STDOUT, text=True)
    return int(p.returncode)


def _report_path(scope: str) -> Path:
    return MODEL_DIR / f"{scope.upper().replace('.', '_')}_training_report_latest.json"


def _sync_report(scope: str, log_handle) -> bool:
    path = _report_path(scope)
    if not path.exists():
        log_handle.write(f"[CLOUD] report not found: {path}\n")
        return False
    try:
        report: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        cfg = load_bridge_config()
        bridge = CloudBridge(cfg, timeout=8.0)
        payload = {
            "bridge_id": cfg.bridge_id,
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
        log_handle.write(
            f"[CLOUD] synced scope={payload['scope']} maturity={payload['maturity']} "
            f"nonoverlap={payload['samples_test_nonoverlap']}\n"
        )
        log_handle.flush()
        return True
    except Exception as exc:
        log_handle.write(f"[CLOUD] sync failed; local report preserved: {exc}\n")
        log_handle.flush()
        return False


def _train(now: datetime, slot: str) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    log_path = MODEL_DIR / f"autotrain_{now.strftime('%Y-%m-%d')}_{slot}.log"
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n=== auto train {datetime.now().astimezone().isoformat(timespec='seconds')} ===\n")
        rc_priority = _run_one("301236.SZ", 200, log)
        if rc_priority == 0:
            _sync_report("301236.SZ", log)
        rc_pooled = _run_one("ALL", 300, log)
        if rc_pooled == 0:
            _sync_report("ALL", log)
        log.write(f"\npriority_rc={rc_priority} pooled_rc={rc_pooled}\n")
        log.write("Research only. No live deployment. Successful reports are synced to Supabase.\n")


def main() -> None:
    print("AStock L2 ML auto-trainer started")
    print("Schedules: 11:35 lunch + 15:10 after close. Reports sync to cloud; no auto deployment.")
    while True:
        now = datetime.now()
        slot = _slot(now)
        if slot:
            key = f"{now.strftime('%Y-%m-%d')}:{slot}"
            state = _load_state()
            if state.get("last_slot") != key:
                try:
                    _train(now, slot)
                    state["last_slot"] = key
                    state["last_finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                    _save_state(state)
                except Exception as exc:
                    print(f"[WARN] auto-train failed: {exc}")
        time.sleep(CHECK_SECONDS)


if __name__ == "__main__":
    main()
