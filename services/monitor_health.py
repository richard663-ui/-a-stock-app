"""Local read-only health report; never confuse a heartbeat with new samples."""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.feed_health import tick_is_fresh, tick_timestamp
from modules.market_clock import market_now


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def report(data_root: Path, probe_qmt: bool = True) -> dict[str, Any]:
    now = market_now()
    recorder = read_json(data_root / "training" / "training_status.json")
    try:
        heartbeat_age = time.time() - datetime.fromisoformat(recorder["updated_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        heartbeat_age = None
    progress: dict[str, Any] = {}
    try:
        with (data_root / "reports" / "learning_progress.csv").open(encoding="utf-8-sig", newline="") as stream:
            progress = next(csv.DictReader(stream), {})
    except OSError:
        pass
    minute = now.hour * 60 + now.minute
    scheduled_session = now.weekday() < 5 and (
        555 <= minute < 565 or 570 <= minute < 690 or 780 <= minute < 900
    )
    qmt: dict[str, Any] = {"probed": probe_qmt, "error": "", "feeds": {}}
    if probe_qmt:
        try:
            # xtquant's greeting contains installation details; keep stdout JSON-only.
            with contextlib.redirect_stdout(io.StringIO()):
                from xtquant import xtdata
                xtdata.enable_hello = False
                symbols = recorder.get("symbols") or ["301236.SZ"]
                raw = xtdata.get_full_tick(symbols) or {}
            for symbol in symbols:
                row = raw.get(symbol) or {}
                stamp = tick_timestamp(row)
                qmt["feeds"][symbol] = {
                    "available": bool(row),
                    "fresh": tick_is_fresh(row),
                    "exchange_time": datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp else None,
                }
        except Exception as exc:
            # Avoid dumping config or tracebacks into the user-facing report.
            qmt["error"] = type(exc).__name__
    return {
        "checked_at": now.isoformat(timespec="seconds"),
        "market_timezone": "Asia/Shanghai",
        "scheduled_session": scheduled_session,
        "session_note": "Weekday schedule only; exchange tick freshness also required.",
        "recorder_heartbeat_age_seconds": round(heartbeat_age, 1) if heartbeat_age is not None else None,
        "recorder_heartbeat_fresh": heartbeat_age is not None and -2 <= heartbeat_age <= 45,
        "recorder": recorder,
        "samples_today": sum(recorder.get("sample_counts_today", {}).values()),
        "valid_labels_today": sum(recorder.get("labeled_counts_today", {}).values()),
        "power_guard": recorder.get("power_guard") or {},
        "continuity": recorder.get("continuity") or {},
        "learning": progress,
        "qmt": qmt,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-qmt", action="store_true")
    parser.add_argument("--data-root", type=Path, default=Path(os.environ.get("ASTOCK_RESEARCH_DATA_DIR", str(Path.home() / "AStockData"))))
    args = parser.parse_args()
    print(json.dumps(report(args.data_root, not args.no_qmt), ensure_ascii=True))


if __name__ == "__main__":
    main()
