"""Validate exchange timestamps rather than cache polling timestamps."""

from datetime import datetime
import math
import time
from typing import Any


MAX_TICK_AGE_SECONDS = 12.0


def tick_timestamp(row: dict[str, Any]) -> float | None:
    try:
        value = float(row.get("time", 0))
        if value > 1e12:
            return value / 1000
        if value > 1e9:
            return value
    except (TypeError, ValueError):
        pass
    # Capture time is not evidence of fresh exchange data.
    try:
        parsed = datetime.fromisoformat(str(row.get("timetag", "")).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.timestamp()
    except ValueError:
        pass
    return None


def tick_is_fresh(row: dict[str, Any], now: float | None = None) -> bool:
    timestamp = tick_timestamp(row)
    if timestamp is None or not math.isfinite(timestamp):
        return False
    age = (time.time() if now is None else now) - timestamp
    return -2.0 <= age <= MAX_TICK_AGE_SECONDS
