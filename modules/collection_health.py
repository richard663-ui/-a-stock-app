"""Explain whether the recorder can collect, using exchange-time freshness."""

from datetime import datetime, timezone
from typing import Any

from modules.feed_health import tick_is_fresh, tick_timestamp


def collection_health(
    symbols: list[str],
    ticks: dict[str, dict[str, Any]],
    buffer_lengths: dict[str, int],
    market_open: bool,
    now: float,
) -> dict[str, Any]:
    feeds: dict[str, Any] = {}
    for symbol in symbols:
        row = ticks.get(symbol) or {}
        timestamp = tick_timestamp(row)
        fresh = tick_is_fresh(row, now)
        buffered = buffer_lengths.get(symbol, 0)
        if not row:
            state = "NO_QMT_TICK"
        elif not fresh:
            state = "STALE_EXCHANGE_TICK"
        elif buffered < 20:
            state = "WARMING_UP"
        else:
            state = "READY"
        feeds[symbol] = {
            "state": state,
            "exchange_time": datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
            if timestamp is not None else None,
            "tick_age_seconds": round(now - timestamp, 1) if timestamp is not None else None,
            "buffered_ticks": buffered,
        }
    states = [feed["state"] for feed in feeds.values()]
    if not market_open:
        overall = "MARKET_CLOSED"
    elif "READY" in states:
        overall = "READY" if all(s == "READY" for s in states) else "PARTIAL_FEED"
    elif "WARMING_UP" in states:
        overall = "WARMING_UP"
    else:
        overall = "WAITING_FRESH_TICKS"
    return {"collection_state": overall, "feeds": feeds}
