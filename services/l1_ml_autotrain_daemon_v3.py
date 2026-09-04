# -*- coding: utf-8 -*-
"""L1 auto-trainer V3: robust trainer + cloud-visible subprocess diagnostics."""
from __future__ import annotations

import subprocess
from pathlib import Path

import services.l1_ml_autotrain_daemon_v1 as base

AUTO_TRAINER_VERSION = "l1-ml-autotrain-v3-diagnostic-20260904"
base.AUTO_TRAINER_VERSION = AUTO_TRAINER_VERSION
base.TRAINER = Path(__file__).with_name("train_l1_60s_model_v3.py")
_LAST_TAIL = ""


def _run_one(scope: str, minimum: int, log_handle) -> int:
    global _LAST_TAIL
    cmd = [
        base.sys.executable, str(base.TRAINER), "--symbol", scope,
        "--min-samples", str(minimum), "--hurdle-bp", "2.0",
    ]
    log_handle.write("\n$ " + " ".join(cmd) + "\n")
    log_handle.flush()
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    output = p.stdout or ""
    log_handle.write(output)
    log_handle.flush()
    # Keep a compact cloud-visible tail; strip newlines so status remains readable.
    compact = " | ".join(x.strip() for x in output.splitlines()[-12:] if x.strip())
    _LAST_TAIL = compact[-700:]
    return int(p.returncode)


def _result(rc: int, synced: bool) -> str:
    if rc == 0 and synced:
        return "TRAINED_SYNCED"
    if rc == 0:
        return "TRAINED_LOCAL_SYNC_FAILED"
    suffix = _LAST_TAIL.replace("\n", " ")[-500:]
    return f"TRAIN_FAILED_RC_{rc}: {suffix}" if suffix else f"TRAIN_FAILED_RC_{rc}"


base._run_one = _run_one
base._result = _result


def main() -> None:
    print("AStock L1/Tick 60s ML auto-trainer V3 started")
    print(f"Daemon: {AUTO_TRAINER_VERSION}")
    print("Robust standalone fallback enabled; failure tail is synced to cloud status.")
    base.main()


if __name__ == "__main__":
    main()
