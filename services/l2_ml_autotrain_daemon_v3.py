# -*- coding: utf-8 -*-
"""Morning-aware wrapper for the existing ML auto-trainer heartbeat daemon."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Tuple

import services.l2_ml_autotrain_daemon as base

AUTO_TRAINER_VERSION = "l2-ml-autotrain-v3-morning-gated-20260903"
TRAINER = Path(__file__).with_name("train_l2_60s_model_v5.py")

base.AUTO_TRAINER_VERSION = AUTO_TRAINER_VERSION
base.TRAINER = TRAINER


def _eligible_count(scope: str) -> int:
    """Count only continuous-auction fresh-L2 labels; opening auction stays separate."""
    total = 0
    for path in sorted((base.DATA_ROOT / "training").glob("*/l2_training.sqlite3")):
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
            try:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_samples_v2'"
                ).fetchone()
                if not exists:
                    continue
                where = (
                    "valid=1 AND true_l2=1 AND labeled_at IS NOT NULL "
                    "AND label_smoothed_mid_60 IN (-1,0,1) "
                    "AND upper(coalesce(session,'')) <> 'OPEN_AUCTION'"
                )
                args: Tuple[Any, ...] = ()
                if scope.upper() != "ALL":
                    where += " AND upper(symbol)=?"
                    args = (scope.upper(),)
                row = conn.execute(f"SELECT count(*) FROM training_samples_v2 WHERE {where}", args).fetchone()
                total += int(row[0] if row else 0)
            finally:
                conn.close()
        except Exception:
            continue
    return total


base._eligible_count = _eligible_count


def main() -> None:
    print("Morning-priority ML auto-trainer wrapper active")
    print(f"Daemon: {AUTO_TRAINER_VERSION}")
    print("Opening auction is excluded from the continuous model; 09:30-10:30 OOS gate is mandatory.")
    base.main()


if __name__ == "__main__":
    main()
