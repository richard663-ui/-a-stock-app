# -*- coding: utf-8 -*-
"""Compatibility launcher: V2 filename, asymmetric V4 L1 auto-trainer runtime.

The existing updater still downloads this filename. At runtime it ensures the
V4 asymmetric trainer is present, captures subprocess output tails, and exposes
failures through the existing cloud status row. Keeping the filename stable
avoids another user-facing BAT/update path.
"""
from __future__ import annotations

import subprocess
import urllib.request
from pathlib import Path

import services.l1_ml_autotrain_daemon_v1 as base

COMPAT_MARKER = "l1-ml-autotrain-v2-dedup-20260904"
AUTO_TRAINER_VERSION = "l1-ml-autotrain-v4-asymmetric-regime-20260904"
V4_MARKER = "l1-60s-trainer-v4-asymmetric-regime-20260904"
V4_URL = "https://raw.githubusercontent.com/richard663-ui/-a-stock-app/main/services/train_l1_60s_model_v4.py"
V4_PATH = Path(__file__).with_name("train_l1_60s_model_v4.py")
_LAST_TAIL = ""


def _ensure_v4() -> None:
    try:
        if V4_PATH.exists() and V4_MARKER in V4_PATH.read_text(encoding="utf-8", errors="ignore"):
            return
    except Exception:
        pass
    with urllib.request.urlopen(V4_URL, timeout=15) as resp:
        data = resp.read()
    text = data.decode("utf-8")
    if V4_MARKER not in text:
        raise RuntimeError("downloaded V4 trainer failed marker check")
    tmp = V4_PATH.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(V4_PATH)


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
    compact = " | ".join(x.strip() for x in output.splitlines()[-16:] if x.strip())
    _LAST_TAIL = compact[-900:]
    return int(p.returncode)


def _result(rc: int, synced: bool) -> str:
    if rc == 0 and synced:
        return "TRAINED_SYNCED"
    if rc == 0:
        return "TRAINED_LOCAL_SYNC_FAILED"
    suffix = _LAST_TAIL.replace("\n", " ")[-650:]
    return f"TRAIN_FAILED_RC_{rc}: {suffix}" if suffix else f"TRAIN_FAILED_RC_{rc}"


def main() -> None:
    print("AStock L1/Tick 60s ML auto-trainer V4 started")
    print(f"Daemon: {AUTO_TRAINER_VERSION}")
    print(f"Compatibility marker: {COMPAT_MARKER}")
    _ensure_v4()
    base.AUTO_TRAINER_VERSION = AUTO_TRAINER_VERSION
    base.TRAINER = V4_PATH
    base._run_one = _run_one
    base._result = _result
    print("Asymmetric UP-entry / DOWN-risk trainer enabled. No live deployment.")
    base.main()


if __name__ == "__main__":
    main()
