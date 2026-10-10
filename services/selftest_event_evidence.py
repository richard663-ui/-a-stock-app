import unittest
from datetime import datetime, timezone
from modules.event_evidence import inspect_event, summarize_events


class EventEvidenceTests(unittest.TestCase):
    now = datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)

    def event(self, **changes):
        return {"title": "Fictional test event; not investment evidence", "source": "Test fixture",
                "source_url": "https://www.cninfo.com.cn/test-only", "published_at": "2026-10-09T01:00:00Z",
                "verified_at": "2026-10-09T01:30:00Z", "verified": True,
                "expected_impact": "positive", "related_to_stock": True, **changes}

    def test_no_keyword_verification(self):
        result = inspect_event({"title": "公告 订单 回购 英伟达"}, self.now)
        self.assertFalse(result["source_verified"])
        self.assertFalse(result["eligible_for_catalyst"])

    def test_source_and_time_required(self):
        for changes in [{"source_url": ""}, {"source": ""}, {"verified": False},
                        {"published_at": "2026-10-09T03:00:00Z"},
                        {"published_at": "2026-10-09T01:00:00"},
                        {"published_at": "2026-09-01T01:00:00Z"},
                        {"verified_at": "2026-10-09T03:00:00Z"}, {"related_to_stock": False}]:
            with self.subTest(changes=changes):
                self.assertFalse(inspect_event(self.event(**changes), self.now)["eligible_for_catalyst"])

    def test_domain_spoof_and_google_link(self):
        for url in ["https://cninfo.com.cn.evil.example/doc", "https://news.google.com/rss/articles/test",
                    "https://example.com/?source=cninfo.com.cn", "http://www.cninfo.com.cn/test-only"]:
            self.assertFalse(inspect_event(self.event(source_url=url), self.now)["primary_source"])

    def test_negative_and_mixed_not_positive_bonus(self):
        for title in ["示例公司宣布减持", "示例公司订单增加但净利润下滑"]:
            result = inspect_event(self.event(title=title), self.now)
            self.assertEqual(result["expected_impact"], "mixed")
            self.assertFalse(result["eligible_for_catalyst"])

    def test_dedup_and_unverified_rss(self):
        result = summarize_events([self.event(), self.event(), self.event(title="RSS test", verified=False)], self.now)
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["verified_positive_count"], 1)
        self.assertEqual(result["score"], 40)
        self.assertFalse(result["active_for_short_horizon_signals"])
        self.assertEqual(summarize_events([], self.now)["score"], 0)


if __name__ == "__main__":
    unittest.main()
