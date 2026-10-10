"""Inspect labeled research data and decide whether a new fit is warranted."""

from __future__ import annotations

import hashlib
from contextlib import closing
import json
import sqlite3
import csv
import os
from tempfile import NamedTemporaryFile
from datetime import datetime
from pathlib import Path

from modules.market_clock import market_now, market_from_timestamp


MIN_NEW_SAMPLES = 100
MAX_DATA_AGE_DAYS = 7


def inspect_learning_data(data_root: Path, priority_symbol: str) -> dict:
    records = []
    errors = []
    latest_timestamp = None
    pooled = priority = 0
    for path in sorted((data_root / "training").glob("*/l2_training.sqlite3")):
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                if not db.execute("select 1 from sqlite_master where name='training_samples_v2'").fetchone():
                    continue
                rows = db.execute("""
                    select upper(symbol),count(*),max(generated_ts),sum(label_smoothed_mid_60)
                    from (
                        select symbol,sample_bucket,max(generated_ts) as generated_ts,
                               max(label_smoothed_mid_60) as label_smoothed_mid_60
                        from training_samples_v2
                        where valid=1 and labeled_at is not null
                          and label_smoothed_mid_60 in (-1,0,1)
                          and upper(coalesce(session,'')) <> 'OPEN_AUCTION'
                        group by symbol,sample_bucket
                    ) group by upper(symbol)
                """).fetchall()
                for symbol, count, latest, label_sum in rows:
                    pooled += count
                    if symbol == priority_symbol:
                        priority += count
                    latest_timestamp = max(latest_timestamp or latest, latest)
                    records.append([path.parent.name, symbol, count, latest, label_sum])
        except (sqlite3.Error, OSError, TypeError) as exc:
            errors.append(f"{path.parent.name}: {type(exc).__name__}")
    latest = market_from_timestamp(latest_timestamp) if latest_timestamp else None
    return {
        "priority_samples": priority,
        "pooled_samples": pooled,
        "latest_sample_at": latest.isoformat() if latest else None,
        "data_latest_date": latest.date().isoformat() if latest else None,
        "data_days": sorted({row[0] for row in records}),
        "dataset_fingerprint": hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest(),
        "scan_errors": errors,
    }


def learning_progress(snapshot: dict, state: dict, report: dict | None = None,
                      now: datetime | None = None) -> dict:
    report = report or {}
    latest = snapshot.get("latest_sample_at")
    trained_count = int(state.get("last_trained_samples", report.get("samples_total", 0)) or 0)
    count = int(snapshot.get("pooled_samples", 0))
    age = (market_now(now).date() - market_now(datetime.fromisoformat(latest)).date()).days if latest else None
    increment = max(0, count - trained_count)
    result = {**snapshot, "new_samples_since_last_training": increment,
              "data_age_days": age, "min_new_samples": MIN_NEW_SAMPLES,
              "market_timezone": "Asia/Shanghai", "data_mode": "L1_BASELINE",
              "last_training_at": report.get("generated_at"),
              "evaluation_protocol": report.get("protocol"), "auto_deployed": False,
              "eligible_for_live_deployment": False}
    if snapshot.get("scan_errors"):
        decision = "DATA_SCAN_ERROR"
    elif not latest:
        decision = "WAITING_DATA"
    elif age is None or age < 0 or age > MAX_DATA_AGE_DAYS:
        decision = "WAITING_FRESH_DATA"
    elif state.get("trained_dataset_fingerprint") == snapshot.get("dataset_fingerprint"):
        decision = "WAITING_NEW_SAMPLES"
    elif trained_count and increment < MIN_NEW_SAMPLES:
        decision = "WAITING_NEW_SAMPLES"
    else:
        decision = "READY_TO_TRAIN"
    result["learning_state"] = decision
    result["can_train"] = decision == "READY_TO_TRAIN"
    result["reason"] = {
        "DATA_SCAN_ERROR": "Local dataset scan failed; training is paused.",
        "WAITING_DATA": "No valid labeled samples yet; recorder must collect fresh data.",
        "WAITING_FRESH_DATA": "Labeled data is stale; new report timestamps do not mean new learning.",
        "WAITING_NEW_SAMPLES": "Waiting for at least 100 new valid labeled samples; old data will not be repeatedly fitted.",
        "READY_TO_TRAIN": "New labeled samples available; fit at the next Shanghai lunch/close slot.",
    }[decision]
    return result


def export_learning_progress(progress: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["learning_state", "data_latest_date", "latest_sample_at", "pooled_samples",
              "priority_samples", "new_samples_since_last_training", "last_training_at",
              "evaluation_protocol", "data_mode", "market_timezone", "can_train",
              "eligible_for_live_deployment", "reason"]
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", dir=path.parent,
                                delete=False, suffix=".csv") as file:
            temporary = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerow({field: progress.get(field) for field in fields})
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
