# -*- coding: utf-8 -*-
"""Compatibility launcher: V4R Champion + V5R/V6 research challengers.

The stable updater still downloads this V2 filename. V4R remains Champion and
is trained/synced exactly as before. V5R and V6 run afterwards into separate
artifact directories/cloud scopes. Challenger failures never replace or break
the Champion. Nothing is auto-promoted or auto-deployed.
"""
from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Tuple

import services.l1_ml_autotrain_daemon_v1 as base

COMPAT_MARKER = "l1-ml-autotrain-v2-dedup-20260904"
AUTO_TRAINER_VERSION = "l1-ml-autotrain-v6-multi-challenger-20260905"
CORE_MARKER = "l1-60s-trainer-v4-asymmetric-regime-20260904"
RUNNER_MARKER = "l1-60s-trainer-v4r-asymmetric-rotating-thin-20260904"
V5_MARKER = "l1-60s-trainer-v5-robust-challenger-20260904"
V5R_MARKER = "l1-60s-trainer-v5r-robust-challenger-20260904"
V6_MARKER = "l1-60s-trainer-v6-exec-aligned-stock-intercept-robust-20260905"
ROOT = "https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/services"
CORE_PATH = Path(__file__).with_name("train_l1_60s_model_v4.py")
RUNNER_PATH = Path(__file__).with_name("train_l1_60s_model_v4r.py")
V5_PATH = Path(__file__).with_name("train_l1_60s_model_v5_challenger.py")
V5R_PATH = Path(__file__).with_name("train_l1_60s_model_v5r.py")
V6_PATH = Path(__file__).with_name("train_l1_60s_model_v6_exec_aligned.py")
V5_MODEL_DIR = base.MODEL_DIR / "v5_challenger"
V6_MODEL_DIR = base.MODEL_DIR / "v6_exec_aligned"
_LAST_TAIL = ""
_LAST_V5 = "NOT_RUN"
_LAST_V6 = "NOT_RUN"
_BASE_SLOT = base._slot


def _ensure(path: Path, url: str, marker: str) -> None:
    try:
        if path.exists() and marker in path.read_text(encoding="utf-8", errors="ignore"):
            return
    except Exception:
        pass
    with urllib.request.urlopen(url, timeout=15) as resp:
        text = resp.read().decode("utf-8")
    if marker not in text:
        raise RuntimeError(f"downloaded trainer failed marker check: {path.name}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _ensure_stack() -> None:
    _ensure(CORE_PATH, f"{ROOT}/train_l1_60s_model_v4.py", CORE_MARKER)
    _ensure(RUNNER_PATH, f"{ROOT}/train_l1_60s_model_v4r.py", RUNNER_MARKER)
    _ensure(V5_PATH, f"{ROOT}/train_l1_60s_model_v5_challenger.py", V5_MARKER)
    _ensure(V5R_PATH, f"{ROOT}/train_l1_60s_model_v5r.py", V5R_MARKER)
    _ensure(V6_PATH, f"{ROOT}/train_l1_60s_model_v6_exec_aligned.py", V6_MARKER)


def _run_process(path: Path, scope: str, minimum: int, log_handle) -> Tuple[int, str]:
    cmd = [
        base.sys.executable, str(path), "--symbol", scope,
        "--min-samples", str(minimum), "--hurdle-bp", "2.0",
    ]
    log_handle.write("\n$ " + " ".join(cmd) + "\n")
    log_handle.flush()
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    output = p.stdout or ""
    log_handle.write(output)
    log_handle.flush()
    compact = " | ".join(x.strip() for x in output.splitlines()[-20:] if x.strip())
    return int(p.returncode), compact[-1200:]


def _report_path(model_dir: Path, scope: str) -> Path:
    return model_dir / f"{scope.upper().replace('.', '_')}_training_report_latest.json"


def _sync_report(scope: str, model_dir: Path, suffix: str, tag: str, log_handle) -> bool:
    path = _report_path(model_dir, scope)
    if not path.exists():
        log_handle.write(f"[{tag} CLOUD] report not found: {path}\n")
        return False
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        bridge, bridge_id = base._cloud()
        cloud_scope = f"{str(report.get('scope') or scope.upper())}::{suffix}"
        report["cloud_scope"] = cloud_scope
        payload = {
            "bridge_id": bridge_id,
            "scope": cloud_scope,
            "trainer_version": str(report.get("trainer_version") or "unknown"),
            "generated_at": report.get("generated_at"),
            "maturity": report.get("maturity"),
            "protocol": report.get("protocol"),
            "samples_total": int(report.get("samples_total") or 0),
            "samples_test_nonoverlap": int(report.get("samples_test_nonoverlap") or 0),
            "report": report,
        }
        bridge._request(
            "POST", "ml_training_reports_v1?on_conflict=bridge_id,scope,generated_at",
            json=payload,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        log_handle.write(f"[{tag} CLOUD] synced scope={cloud_scope}\n")
        return True
    except Exception as exc:
        log_handle.write(f"[{tag} CLOUD] sync failed; local report preserved: {exc}\n")
        return False


def _run_challenger(path: Path, model_dir: Path, suffix: str, tag: str,
                    scope: str, minimum: int, log_handle) -> str:
    try:
        rc, tail = _run_process(path, scope, minimum, log_handle)
        if rc != 0:
            return f"FAILED_RC_{rc}:{tail[-350:]}"
        synced = _sync_report(scope, model_dir, suffix, tag, log_handle)
        return "TRAINED_SYNCED" if synced else "TRAINED_LOCAL_SYNC_FAILED"
    except Exception as exc:
        log_handle.write(f"[{tag} WARN] challenger exception: {exc}\n")
        return f"EXCEPTION:{type(exc).__name__}:{exc}"


def _run_one(scope: str, minimum: int, log_handle) -> int:
    global _LAST_TAIL, _LAST_V5, _LAST_V6
    champion_rc, champion_tail = _run_process(RUNNER_PATH, scope, minimum, log_handle)
    _LAST_TAIL = champion_tail

    # Each challenger is isolated in a subprocess and separate artifact folder.
    # Neither can alter the Champion result or each other.
    _LAST_V5 = _run_challenger(
        V5R_PATH, V5_MODEL_DIR, "V5_CHALLENGER", "V5", scope, minimum, log_handle
    )
    _LAST_V6 = _run_challenger(
        V6_PATH, V6_MODEL_DIR, "V6_CHALLENGER", "V6", scope, minimum, log_handle
    )
    return champion_rc


def _result(rc: int, synced: bool) -> str:
    challengers = f"V5={_LAST_V5};V6={_LAST_V6}"
    if rc == 0 and synced:
        return f"TRAINED_SYNCED;{challengers}"
    if rc == 0:
        return f"TRAINED_LOCAL_SYNC_FAILED;{challengers}"
    suffix = _LAST_TAIL.replace("\n", " ")[-650:]
    return f"TRAIN_FAILED_RC_{rc}: {suffix};{challengers}" if suffix else f"TRAIN_FAILED_RC_{rc};{challengers}"


def _extended_slot(now) -> str:
    normal = _BASE_SLOT(now)
    if normal:
        return normal
    if now.weekday() >= 5:
        return ""
    minute = now.hour * 60 + now.minute
    if 15 * 60 + 10 <= minute < 23 * 60:
        return "PM"
    return ""


def main() -> None:
    print("AStock L1/Tick ML champion-multi-challenger daemon started")
    print(f"Daemon: {AUTO_TRAINER_VERSION}")
    print(f"Compatibility marker: {COMPAT_MARKER}")
    _ensure_stack()
    base.AUTO_TRAINER_VERSION = AUTO_TRAINER_VERSION
    base.TRAINER = RUNNER_PATH
    base._run_one = _run_one
    base._result = _result
    base._slot = _extended_slot
    print("Champion=V4R. Controls=V5R. New Challenger=V6 execution-aligned. No auto promotion/deployment.")
    base.main()


if __name__ == "__main__":
    main()
