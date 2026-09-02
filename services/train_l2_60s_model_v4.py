# -*- coding: utf-8 -*-
"""V4 wrapper: run V3 60s ML training and sync the finished report to Supabase.

Training logic, splits, labels, models and deployment rules remain in V3.
This wrapper only publishes the completed JSON report so remote validation can
be inspected without touching the local Windows machine.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import services.train_l2_60s_model_v3 as base
from modules.cloud_bridge import CloudBridge, load_bridge_config

TRAINER_VERSION = "l2-60s-trainer-v4-cloud-report-20260902"


def _report_path(symbol: str) -> Path:
    scope = symbol.upper().replace(".", "_")
    return base.MODEL_DIR / f"{scope}_training_report_latest.json"


def _sync_report(symbol: str) -> bool:
    path = _report_path(symbol)
    if not path.exists():
        print(f"[CLOUD] Report not found: {path}")
        return False
    try:
        report: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[CLOUD] Could not read report: {exc}")
        return False

    try:
        cfg = load_bridge_config()
        bridge = CloudBridge(cfg, timeout=8.0)
        payload = {
            "bridge_id": cfg.bridge_id,
            "scope": str(report.get("scope") or symbol.upper()),
            "trainer_version": str(report.get("trainer_version") or TRAINER_VERSION),
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
        print(f"[CLOUD] ML report synced: scope={payload['scope']} maturity={payload['maturity']}")
        return True
    except Exception as exc:
        # Cloud reporting must never invalidate a locally successful training run.
        print(f"[CLOUD] Report sync failed; local report is still valid: {exc}")
        return False


def train(symbol: str, min_samples: int, data_root: Path) -> int:
    rc = int(base.train(symbol, min_samples, data_root))
    if rc == 0:
        _sync_report(symbol)
    return rc


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=base.PRIMARY_SYMBOL)
    p.add_argument("--min-samples", type=int, default=200)
    p.add_argument("--data-root", default=str(base.DATA_ROOT))
    a = p.parse_args()
    raise SystemExit(train(a.symbol, a.min_samples, Path(a.data_root).expanduser()))


if __name__ == "__main__":
    main()
