# -*- coding: utf-8 -*-
"""Compatibility launcher: V2 filename, V3 robust L1 auto-trainer runtime.

The existing updater still downloads this filename. At runtime it ensures the
standalone V3 trainer is present, captures the subprocess tail, and exposes that
tail through the existing cloud status fields when training fails.
"""
from __future__ import annotations

import subprocess
import urllib.request
from pathlib import Path

import services.l1_ml_autotrain_daemon_v1 as base

COMPAT_MARKER = "l1-ml-autotrain-v2-dedup-20260904"
AUTO_TRAINER_VERSION = "l1-ml-autotrain-v3-diagnostic-20260904"
V3_MARKER = "l1-60s-trainer-v3-robust-standalone-20260904"
V3_URL = "https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/services/train_l1_60s_model_v3.py"
V3_PATH = Path(__file__).with_name("train_l1_60s_model_v3.py")
_LAST_TAIL = ""


def _ensure_v3() -> None:
    try:
        if V3_PATH.exists() and V3_MARKER in V3_PATH.read_text(encoding="utf-8", errors="ignore"):
            return
    except Exception:
        pass
    with urllib.request.urlopen(V3_URL, timeout=15) as resp:
        data = resp.read()
    text = data.decode("utf-8")
    if V3_MARKER not in text:
        raise RuntimeError("downloaded V3 trainer failed marker check")
    tmp = V3_PATH.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(V3_PATH)


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


def main() -> None:
    print("AStock L1/Tick 60s ML auto-trainer V3 started")
    print(f"Daemon: {AUTO_TRAINER_VERSION}")
    print(f"Compatibility marker: {COMPAT_MARKER}")
    _ensure_v3()
    base.AUTO_TRAINER_VERSION = AUTO_TRAINER_VERSION
    base.TRAINER = V3_PATH
    base._run_one = _run_one
    base._result = _result
    print("Robust standalone fallback enabled; failure tail is synced to cloud status.")
    base.main()


if __name__ == "__main__":
    main()
