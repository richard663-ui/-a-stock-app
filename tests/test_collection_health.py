"""Scenarios where running software must not imply fresh training data."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from modules.collection_health import collection_health
from modules.market_clock import market_now
from services.monitor_health import report


class CollectionHealthTests(unittest.TestCase):
    def test_stale_tick_does_not_become_ready_with_full_buffer(self):
        result = collection_health(["A"], {"A": {"time": 1_800_000_000_000}}, {"A": 200}, True, 1_800_000_060)
        self.assertEqual(result["collection_state"], "WAITING_FRESH_TICKS")
        self.assertEqual(result["feeds"]["A"]["state"], "STALE_EXCHANGE_TICK")

    def test_partial_feed_and_warmup_are_visible(self):
        now = 1_800_000_000
        result = collection_health(["A", "B"], {"A": {"time": now * 1000}}, {"A": 20}, True, now)
        self.assertEqual(result["collection_state"], "PARTIAL_FEED")
        self.assertEqual(result["feeds"]["B"]["state"], "NO_QMT_TICK")
        result = collection_health(["A"], {"A": {"time": now * 1000}}, {"A": 2}, True, now)
        self.assertEqual(result["collection_state"], "WARMING_UP")

    def test_weekend_cached_quote_is_not_reported_as_collection(self):
        result = collection_health(["A"], {"A": {"time": 1_800_000_000_000}}, {"A": 200}, False, 1_800_000_000)
        self.assertEqual(result["collection_state"], "MARKET_CLOSED")

    def test_missing_heartbeat_is_not_healthy_and_london_open_maps_to_shanghai(self):
        # 02:30 UTC in winter is Shanghai 10:30, even on a London computer.
        with tempfile.TemporaryDirectory() as directory, patch(
            "services.monitor_health.market_now",
            return_value=market_now(datetime(2026, 11, 2, 2, 30, tzinfo=timezone.utc)),
        ):
            result = report(Path(directory), probe_qmt=False)
            self.assertFalse(result["recorder_heartbeat_fresh"])
            self.assertEqual(result["samples_today"], 0)
            self.assertTrue(result["scheduled_session"])


if __name__ == "__main__":
    unittest.main()
