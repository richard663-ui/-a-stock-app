# -*- coding: utf-8 -*-
"""Auditable 60-second prediction journal for V18.

Predictions are written locally on the ROG and labelled after the 60-second
horizon. The database stores the feature snapshot used at prediction time so
future models can be trained without reconstructing history from screenshots.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict


class PredictionJournal:
    def __init__(self, path: str | Path = "runtime/one_minute_predictions.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5.0)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    bucket_ts INTEGER NOT NULL,
                    created_ts REAL NOT NULL,
                    price REAL NOT NULL,
                    direction TEXT NOT NULL,
                    agreement INTEGER NOT NULL,
                    high_confidence INTEGER NOT NULL DEFAULT 0,
                    true_l2 INTEGER NOT NULL DEFAULT 0,
                    feature_json TEXT NOT NULL DEFAULT '{}',
                    future_ts REAL,
                    future_price REAL,
                    future_return_pct REAL,
                    actual_direction TEXT,
                    correct INTEGER,
                    matured INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(symbol, bucket_ts)
                );
                CREATE INDEX IF NOT EXISTS idx_pred_symbol_matured
                    ON predictions(symbol, matured, created_ts);
                CREATE INDEX IF NOT EXISTS idx_pred_high_conf
                    ON predictions(symbol, high_confidence, true_l2, matured, created_ts);
                """
            )

    def record(
        self,
        *,
        symbol: str,
        price: float,
        direction: str,
        agreement: int,
        high_confidence: bool,
        true_l2: bool,
        features: Dict[str, Any] | None = None,
        now_ts: float | None = None,
        bucket_seconds: int = 10,
    ) -> None:
        if direction not in {"UP", "DOWN"} or float(price or 0) <= 0:
            return
        now_ts = float(now_ts or time.time())
        bucket_ts = int(now_ts // bucket_seconds * bucket_seconds)
        payload = json.dumps(features or {}, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._connect() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO predictions (
                    symbol, bucket_ts, created_ts, price, direction,
                    agreement, high_confidence, true_l2, feature_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(symbol).upper(), bucket_ts, now_ts, float(price), direction,
                    int(agreement), 1 if high_confidence else 0,
                    1 if true_l2 else 0, payload,
                ),
            )

    def mature(
        self,
        *,
        symbol: str,
        current_price: float,
        now_ts: float | None = None,
        horizon_seconds: int = 60,
        flat_band_pct: float = 0.01,
    ) -> int:
        """Label all predictions whose horizon has elapsed using the first loop price after due time.

        A tiny flat band avoids counting effectively unchanged prices as a lucky
        directional hit. Flat observations are matured but excluded from accuracy.
        """
        current_price = float(current_price or 0)
        if current_price <= 0:
            return 0
        now_ts = float(now_ts or time.time())
        cutoff = now_ts - int(horizon_seconds)
        updated = 0
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT id, price, direction FROM predictions
                WHERE symbol = ? AND matured = 0 AND created_ts <= ?
                ORDER BY created_ts ASC LIMIT 1000
                """,
                (str(symbol).upper(), cutoff),
            ).fetchall()
            for row in rows:
                start = float(row["price"] or 0)
                if start <= 0:
                    continue
                ret = (current_price / start - 1.0) * 100.0
                if ret > flat_band_pct:
                    actual = "UP"
                elif ret < -flat_band_pct:
                    actual = "DOWN"
                else:
                    actual = "FLAT"
                correct = None if actual == "FLAT" else int(actual == row["direction"])
                con.execute(
                    """
                    UPDATE predictions
                    SET future_ts=?, future_price=?, future_return_pct=?,
                        actual_direction=?, correct=?, matured=1
                    WHERE id=?
                    """,
                    (now_ts, current_price, ret, actual, correct, int(row["id"])),
                )
                updated += 1
        return updated

    def stats(self, symbol: str, limit: int = 5000) -> Dict[str, Any]:
        symbol = str(symbol).upper()

        def query(where_extra: str = "") -> tuple[int, int, float | None]:
            with self._connect() as con:
                row = con.execute(
                    f"""
                    SELECT COUNT(*) AS n,
                           SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) AS wins,
                           AVG(future_return_pct) AS avg_ret
                    FROM (
                        SELECT correct, future_return_pct FROM predictions
                        WHERE symbol=? AND matured=1 AND correct IS NOT NULL {where_extra}
                        ORDER BY created_ts DESC LIMIT ?
                    )
                    """,
                    (symbol, int(limit)),
                ).fetchone()
            n = int(row["n"] or 0)
            wins = int(row["wins"] or 0)
            return n, wins, float(row["avg_ret"]) if row["avg_ret"] is not None else None

        all_n, all_w, all_ret = query()
        high_n, high_w, high_ret = query("AND high_confidence=1")
        l2_n, l2_w, l2_ret = query("AND high_confidence=1 AND true_l2=1")

        def acc(n: int, w: int) -> float | None:
            return (w / n * 100.0) if n else None

        return {
            "all_samples": all_n,
            "all_accuracy_pct": acc(all_n, all_w),
            "all_avg_return_pct": all_ret,
            "high_conf_samples": high_n,
            "high_conf_accuracy_pct": acc(high_n, high_w),
            "high_conf_avg_return_pct": high_ret,
            "true_l2_high_conf_samples": l2_n,
            "true_l2_high_conf_accuracy_pct": acc(l2_n, l2_w),
            "true_l2_high_conf_avg_return_pct": l2_ret,
        }

    def export_csv(self, output: str | Path = "runtime/one_minute_predictions.csv") -> Path:
        output = Path(output)
        with self._connect() as con, output.open("w", encoding="utf-8-sig", newline="") as f:
            rows = con.execute("SELECT * FROM predictions ORDER BY created_ts").fetchall()
            if not rows:
                f.write("")
                return output
            import csv
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))
        return output
