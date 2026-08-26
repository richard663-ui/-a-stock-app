# -*- coding: utf-8 -*-
"""V4b research launcher: V3 integrity + V4 groups/profile + mobile-V8 persistence parity."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import services.qmt_research_recorder_v4 as v4
import modules.research_forward_model_v4b as model

base = v4.base
RECORDER_VERSION = "research-recorder-v4b-20260826"
PRODUCTION_MODEL = "mobile-grouped-v8-stable-exhaustion"

base.RECORDER_VERSION = RECORDER_VERSION
base.MODEL_VERSION = model.MODEL_VERSION
base.PRODUCTION_MODEL = PRODUCTION_MODEL
base.score_rows = model.score_rows
base.score_label = model.score_label
base.high_confidence = model.high_confidence


def _sync_db_v4b(self, path):
    """Durable sync preserving multiple model versions in the same time bucket."""
    if self.bridge is None:
        return
    conn = sqlite3.connect(path, timeout=5.0)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='forward_eval_v3'"
        ).fetchone()
        if not exists:
            return
        rows = conn.execute("""
            SELECT id,symbol,horizon_seconds,bucket_start,generated_at,expires_at,entry_price,exit_price,
                   return_pct,direction,score,score_abs,tier,high_confidence,flat,correct,valid,invalid_reason,
                   score_delay_seconds,actual_horizon_seconds,macd_bias,features_json,scored_at,model_version
            FROM forward_eval_v3
            WHERE cloud_synced=0 AND scored_at IS NOT NULL
            ORDER BY id LIMIT 200
        """).fetchall()
        for r in rows:
            row_model = str(r[23] or base.MODEL_VERSION)
            payload = {
                "bridge_id": self.bridge.config.bridge_id,
                "symbol": r[1], "horizon_seconds": int(r[2]),
                "bucket_start": datetime.fromtimestamp(int(r[3])).astimezone().isoformat(),
                "generated_at": r[4], "expires_at": r[5], "entry_price": r[6], "exit_price": r[7],
                "return_pct": r[8], "direction": r[9], "score": int(r[10]), "score_abs": int(r[11]),
                "tier": r[12], "high_confidence": bool(r[13]),
                "flat": None if r[14] is None else bool(r[14]),
                "correct": None if r[15] is None else bool(r[15]),
                "valid": bool(r[16]), "invalid_reason": r[17],
                "score_delay_seconds": r[18], "actual_horizon_seconds": r[19],
                "model_version": row_model, "macd_bias": r[20],
                "features": json.loads(r[21] or "{}"), "scored_at": r[22],
            }
            self.bridge._request(
                "POST",
                "forward_eval_samples_v2?on_conflict=bridge_id,symbol,horizon_seconds,bucket_start,model_version",
                json=payload,
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
            conn.execute("UPDATE forward_eval_v3 SET cloud_synced=1 WHERE id=?", (r[0],))
            conn.commit()
    finally:
        conn.close()


base.DurableUploader._sync_db = _sync_db_v4b


def main():
    print("V4b active: grouped factors + exhaustion + multi-cycle MACD + 3/4 persistence + volume profile")
    base.main()


if __name__ == "__main__":
    main()
