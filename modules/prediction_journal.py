# -*- coding: utf-8 -*-
"""Local research journal for the 60-second direction engine.

The QMT bridge records live predictions and automatically labels them after
60 seconds. This gives us a real, auditable hit-rate instead of calling a
condition score an "accuracy" number.

No order-routing code lives here. Storage is local SQLite under runtime/.
"""
from __future__ import annotations

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
                    score REAL NOT NULL,
                    high_confidence INTEGER NOT NULL DEFAULT 0,
                    true_l2 INTEGER NOT NULL DEFAULT 0,
                    future_ts REAL,
                    future_price REAL,
                    future_return_pct REAL,
                    actual_direction TEXT,
                    correct INTEGER,
                    matured INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(symbol, bucket_ts)
                );
                CREATE INDEX IF NOT EXISTS idx_predictions_symbol_matured
                    ON predictions(symbol, matured, created_ts);
                CREATE INDEX IF NOT EXISTS idx_predictions_high_conf
                    ON predictions(symbol, high_confidence, matured, created_ts);
                """
            )

    def record(
        self,
        *,
        symbol: str,
        price: float,
        direction: str,
        agreement: int,
        score: float,
        high_confidence: bool,
        true_l2: bool,
        now_ts: float | None = None,
        bucket_seconds: int = 10,
    ) -> None:
        if direction not in {"UP", "DOWN"} or price <= 0:
            return
        now_ts = float(now_ts or time.time())
        bucket_ts = int(now_ts // bucket_seconds * bucket_seconds)
        with self._connect() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO predictions (
                    symbol, bucket_ts, created_ts, price, direction,
                    agreement, score, high_confidence, true_l2
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(symbol).upper(),
                    bucket_ts,
                    now_ts,
                    float(price),
                    direction,
                    int(agreement),
                    float(score),
                    1 if high_confidence else 0,
                    1 if true_l2 else 0,
                ),
            )

    def mature(
        self,
        *,
        symbol: str,
        current_price: float,
        now_ts: float | None = None,
        horizon_seconds: int = 60,
    ) -> int:
        if current_price <= 0:
            return 0
        now_ts = float(now_ts or time.time())
        cutoff = now_ts - horizon_seconds
        updated = 0
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT id, price, direction
                FROM predictions
                WHERE symbol = ? AND matured = 0 AND created_ts <= ?
                ORDER BY created_ts ASC
                LIMIT 500
                """,
                (str(symbol).upper(), cutoff),
            ).fetchall()
            for row in rows:
                start_price = float(row["price"] or 0)
                if start_price <= 0:
                    continue
                future_return = (float(current_price) / start_price - 1.0) * 100.0
                if future_return > 0:
                    actual = "UP"
                elif future_return < 0:
                    actual = "DOWN"
                else:
                    actual = "FLAT"
                correct = None if actual == "FLAT" else int(actual == row["direction"])
                con.execute(
                    """
                    UPDATE predictions
                    SET future_ts = ?, future_price = ?, future_return_pct = ?,
                        actual_direction = ?, correct = ?, matured = 1
                    WHERE id = ?
                    """,
                    (
                        now_ts,
                        float(current_price),
                        float(future_return),
                        actual,
                        correct,
                        int(row["id"]),
                    ),
                )
                updated += 1
        return updated

    def stats(self, symbol: str, limit: int = 1000) -> Dict[str, Any]:
        symbol = str(symbol).upper()
        with self._connect() as con:
            all_row = con.execute(
                """
                SELECT COUNT(*) AS n,
                       SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END) AS wins
                FROM (
                    SELECT correct FROM predictions
                    WHERE symbol = ? AND matured = 1 AND correct IS NOT NULL
                    ORDER BY created_ts DESC LIMIT ?
                )
                """,
                (symbol, int(limit)),
            ).fetchone()
            high_row = con.execute(
                """
                SELECT COUNT(*) AS n,
                       SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END) AS wins
                FROM (
                    SELECT correct FROM predictions
                    WHERE symbol = ? AND matured = 1 AND correct IS NOT NULL
                      AND high_confidence = 1
                    ORDER BY created_ts DESC LIMIT ?
                )
                """,
                (symbol, int(limit)),
            ).fetchone()
            l2_high_row = con.execute(
                """
                SELECT COUNT(*) AS n,
                       SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END) AS wins
                FROM (
                    SELECT correct FROM predictions
                    WHERE symbol = ? AND matured = 1 AND correct IS NOT NULL
                      AND high_confidence = 1 AND true_l2 = 1
                    ORDER BY created_ts DESC LIMIT ?
                )
                """,
                (symbol, int(limit)),
            ).fetchone()

        def _pack(row: sqlite3.Row) -> tuple[int, float | None]:
            n = int(row["n"] or 0)
            wins = int(row["wins"] or 0)
            return n, (wins / n * 100.0 if n else None)

        all_n, all_acc = _pack(all_row)
        high_n, high_acc = _pack(high_row)
        l2_n, l2_acc = _pack(l2_high_row)
        return {
            "all_samples": all_n,
            "all_accuracy_pct": all_acc,
            "high_conf_samples": high_n,
            "high_conf_accuracy_pct": high_acc,
            "true_l2_high_conf_samples": l2_n,
            "true_l2_high_conf_accuracy_pct": l2_acc,
        }
