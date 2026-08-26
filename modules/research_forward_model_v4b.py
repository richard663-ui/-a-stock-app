# -*- coding: utf-8 -*-
"""Production-parity persistence gate on top of the V4 grouped score.

Requires 3 of 4 trailing score snapshots to support the same direction before
emitting a directional research score. This mirrors mobile V8's anti-flip gate.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable

import modules.research_forward_model_v4 as core

MODEL_VERSION = "research-shadow-v4b-grouped-stable-exhaustion-persistence"


def score_rows(rows: Iterable[Dict[str, Any]], macd_bias: float = 0.0) -> Dict[str, Any]:
    out = core.score_rows(rows, macd_bias)
    s60 = list(out.get("score60_samples") or [])
    s120 = list(out.get("score120_samples") or [])
    up60 = sum(float(x) >= 15 for x in s60)
    down60 = sum(float(x) <= -15 for x in s60)
    up120 = sum(float(x) >= 15 for x in s120)
    down120 = sum(float(x) <= -15 for x in s120)

    score60 = int(out.get("score60") or 0)
    score120 = int(out.get("score120") or 0)
    if score60 >= 15 and up60 < 3:
        score60 = 0
    elif score60 <= -15 and down60 < 3:
        score60 = 0
    if score120 >= 15 and up120 < 3:
        score120 = 0
    elif score120 <= -15 and down120 < 3:
        score120 = 0

    out.update({
        "score60": score60,
        "score120": score120,
        "direction_support_60": {"up": up60, "down": down60},
        "direction_support_120": {"up": up120, "down": down120},
        "persistence_gate": "3_of_4",
        "model_version": MODEL_VERSION,
    })
    return out


def score_label(score: int) -> Dict[str, Any]:
    return core.score_label(score)


def high_confidence(horizon: int, metrics: Dict[str, Any]) -> bool:
    return False
