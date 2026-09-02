# -*- coding: utf-8 -*-
"""V4 launcher for the L2 ML recorder with lower CPU/SQLite contention.

The 5-second training sample cadence and +60s labels are unchanged. The changes
only reduce background load: Level-2 analysis is refreshed every 1.5 seconds
instead of every 1.0 second, and raw event persistence uses one batch INSERT per
period instead of hundreds of individual execute() calls.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable

import services.qmt_l2_training_recorder_v3 as v3

base = v3.base
RECORDER_VERSION = "l2-training-recorder-v4-efficient-20260902"

# A 5-second feature sample does not benefit materially from eight full L2
# re-analyses every second. 1.5s keeps L2 state fresh while cutting burst load.
base.L2_SNAPSHOT_SECONDS = 1.5
base.RECORDER_VERSION = RECORDER_VERSION


def _batch_insert_events(self, symbol: str, period: str, rows: Iterable[Dict[str, Any]]) -> int:
    conn = self.ensure()
    now_text = datetime.now().astimezone().isoformat(timespec="milliseconds")
    payloads = []
    for row in rows:
        if not isinstance(row, dict) or not row:
            continue
        payloads.append((
            symbol,
            period,
            base._event_key(symbol, period, row),
            now_text,
            base._json(row),
            RECORDER_VERSION,
        ))
    if not payloads:
        return 0
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO l2_events_v2(symbol,period,event_key,captured_at,payload_json,recorder_version) VALUES(?,?,?,?,?,?)",
        payloads,
    )
    inserted = int(conn.total_changes - before)
    if inserted:
        conn.commit()
    return inserted


base.DailyStore.insert_events = _batch_insert_events


def main() -> None:
    print("AStock L2 training recorder V4 efficiency mode")
    print(f"Recorder: {RECORDER_VERSION}")
    print("5s samples and +60s smoothed-mid labels are unchanged.")
    print("L2 snapshot cadence=1.5s; raw events use batched SQLite writes.")
    base.main()


if __name__ == "__main__":
    main()
