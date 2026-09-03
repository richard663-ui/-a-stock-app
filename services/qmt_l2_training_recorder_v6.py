# -*- coding: utf-8 -*-
"""V6 L2 recorder: preserve opening-auction data and tag morning regimes.

Opening auction (09:15-09:25) is recorded as a separate regime and is NOT mixed
blindly with continuous-auction training. This wrapper also publishes a compact
recorder heartbeat so remote checks can verify the actual ML recorder, not just
the separate cloud bridge process.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

import services.qmt_l2_training_recorder_v5 as v5
from modules.cloud_bridge import CloudBridge, load_bridge_config

base = v5.base
RECORDER_VERSION = "l2-training-recorder-v6-morning-auction-20260903b"
base.RECORDER_VERSION = RECORDER_VERSION

_regular_market_open = base._market_open
_regular_session = base._session
_base_feature_snapshot = base._feature_snapshot
_base_write_status = base._write_status


def _market_open_with_auction(now=None) -> bool:
    d = now or datetime.now()
    if d.weekday() >= 5:
        return False
    m = d.hour * 60 + d.minute
    return (555 <= m < 565) or _regular_market_open(d)  # 09:15-09:25 + regular session


def _session_with_auction(now=None) -> str:
    d = now or datetime.now()
    m = d.hour * 60 + d.minute
    if 555 <= m < 565:
        return "OPEN_AUCTION"
    return _regular_session(d)


def _feature_snapshot(row, tick_rows, snap):
    features, meta = _base_feature_snapshot(row, tick_rows, snap)
    minute = float(features.get("minute_of_day") or 0.0)
    features.update({
        "phase_open_auction": int(555.0 <= minute < 565.0),
        "phase_open_core": int(570.0 <= minute < 630.0),
        "phase_am_late": int(630.0 <= minute < 690.0),
        "phase_pm": int(780.0 <= minute < 900.0),
    })
    return features, meta


def _write_status(payload: Dict[str, Any]) -> None:
    """Write local status first, then best-effort mirror to Supabase."""
    _base_write_status(payload)
    try:
        cfg = load_bridge_config()
        bridge = CloudBridge(cfg, timeout=5.0)
        cloud_payload = {
            "bridge_id": cfg.bridge_id,
            "recorder_version": str(payload.get("recorder_version") or RECORDER_VERSION),
            "state": "RUNNING",
            "updated_at": payload.get("updated_at") or datetime.now().astimezone().isoformat(timespec="seconds"),
            "market_open": bool(payload.get("market_open")),
            "symbols": payload.get("symbols") or [],
            "sample_counts_today": payload.get("sample_counts_today") or {},
            "labeled_counts_today": payload.get("labeled_counts_today") or {},
            "raw_l2_events_today": payload.get("raw_l2_events_today") or {},
            "pending_labels": int(payload.get("pending_labels") or 0),
            "status": payload,
        }
        bridge._request(
            "POST",
            "l2_recorder_status_v1?on_conflict=bridge_id",
            json=cloud_payload,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
    except Exception as exc:
        # Recorder must keep collecting even if cloud status sync is unavailable.
        print(f"[WARN] L2 recorder heartbeat sync failed: {exc}")


base._market_open = _market_open_with_auction
base._session = _session_with_auction
base._feature_snapshot = _feature_snapshot
base._write_status = _write_status


def main() -> None:
    print("AStock L2 training recorder V6 morning-priority mode")
    print(f"Recorder: {RECORDER_VERSION}")
    print("09:15-09:25 opening auction is recorded separately.")
    print("09:30-10:30 is tagged as the core morning regime.")
    print("Recorder heartbeat mirrors sample/event counts to Supabase for remote health checks.")
    base.main()


if __name__ == "__main__":
    main()
