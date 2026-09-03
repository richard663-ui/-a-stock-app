# -*- coding: utf-8 -*-
"""V7 L2 ML recorder: external Level2API provider without changing ML labels.

QMT remains the L1/tick source used by the base recorder and +60s label logic.
When ~/.a_stock_qmt/external_l2.toml selects mode=level2api, only the Level-2
manager is replaced by the external gRPC adapter. If the external feed is not
connected, true_l2 stays false and the trainer safely skips those samples.
"""
from __future__ import annotations

from typing import Any, Dict

import services.qmt_l2_training_recorder_v6 as v6
from modules.external_level2 import ExternalLevel2Manager, external_level2_enabled, provider_name

base = v6.base
RECORDER_VERSION = "l2-training-recorder-v7-external-provider-20260903"
base.RECORDER_VERSION = RECORDER_VERSION

if external_level2_enabled():
    base.QMTLevel2Manager = ExternalLevel2Manager

_base_write_status = base._write_status


def _write_status(payload: Dict[str, Any]) -> None:
    payload = dict(payload)
    payload["l2_provider"] = provider_name()
    payload["recorder_version"] = RECORDER_VERSION
    _base_write_status(payload)


base._write_status = _write_status


def main() -> None:
    print("AStock L2 training recorder V7 external-provider mode")
    print(f"Recorder: {RECORDER_VERSION}")
    print(f"L2 provider: {provider_name()}")
    print("QMT L1/ticks still drive price history and +60s labels.")
    print("External L2 failure never falls back to fake L2; true_l2 remains false until real events arrive.")
    base.main()


if __name__ == "__main__":
    main()
