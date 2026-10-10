"""Source-first event checks; keywords alone never verify a company announcement."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

PRIMARY_DOMAINS = {"cninfo.com.cn", "sse.com.cn", "szse.cn", "bse.cn", "gov.cn"}
NEGATIVE_WORDS = ("减持", "预亏", "亏损", "下滑", "取消订单", "终止", "处罚", "立案", "违约")
POSITIVE_WORDS = ("预增", "增长", "回购", "增持", "中标", "重大合同", "订单增加")


def _time(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(str(value))
        except (ValueError, TypeError):
            return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


def inspect_event(row, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Event review time must include timezone.")
    now = now.astimezone(timezone.utc)
    title = str(row.get("title") or "").strip()
    source = str(row.get("source") or "").strip()
    url = str(row.get("source_url") or row.get("link") or "").strip()
    parsed = urlparse(url)
    primary = parsed.scheme == "https" and any(
        parsed.hostname == domain or str(parsed.hostname).endswith("." + domain)
        for domain in PRIMARY_DOMAINS
    )
    published = _time(row.get("published_at") or row.get("pubDate"))
    verified = _time(row.get("verified_at"))
    age = (now - published).total_seconds() if published else None
    warnings = []
    if not title:
        warnings.append("Missing event title.")
    if not source or parsed.scheme not in {"http", "https"} or not parsed.hostname:
        warnings.append("Missing concrete source or URL.")
    if published is None:
        warnings.append("Missing timezone-aware publication time.")
    elif age < 0:
        warnings.append("Future-dated event cannot be used yet.")
    elif age > 72 * 3600:
        warnings.append("Older than 72 hours; archival context, not a fresh catalyst.")
    confirmed = row.get("verified") is True and verified is not None and verified <= now
    if not confirmed:
        warnings.append("Source content has not been explicitly verified.")
    if row.get("related_to_stock") is not True:
        warnings.append("Company relevance has not been confirmed.")
    impact = str(row.get("expected_impact") or "neutral")
    if impact not in {"positive", "negative", "mixed", "neutral"}:
        impact = "neutral"
    negative = any(word in title for word in NEGATIVE_WORDS)
    positive = any(word in title for word in POSITIVE_WORDS)
    if negative and impact == "positive":
        warnings.append("Negative terms conflict with positive interpretation; review required.")
        impact = "mixed"
    elif negative and not positive and impact == "neutral":
        impact = "negative"
    eligible = primary and confirmed and not warnings and impact == "positive"
    level = 1 if primary and confirmed and published and age is not None and age >= 0 else 4
    return {
        **row, "source_url": url,
        "published_at": published.isoformat() if published else None,
        "first_seen_at": row.get("first_seen_at") or now.isoformat(),
        "source_verified": confirmed, "primary_source": primary,
        "certainty_level": level, "expected_impact": impact,
        "freshness_seconds": age, "eligible_for_catalyst": eligible,
        "warnings": warnings,
        "active_for_short_horizon_signals": False,
    }


def summarize_events(rows, now=None):
    events, seen = [], set()
    for row in rows or []:
        key = (str(row.get("title") or "").strip(), str(row.get("link") or row.get("source_url") or ""))
        if key in seen:
            continue
        seen.add(key)
        events.append(inspect_event(row, now))
    verified = [row for row in events if row["eligible_for_catalyst"]]
    return {"events": events, "verified_positive_count": len(verified),
            "score": min(80, len(verified) * 40),
            "warnings": [warning for row in events for warning in row["warnings"]],
            "active_for_short_horizon_signals": False}
