"""RSS / Atom collector for news and PR feeds (e.g. PR Newswire, company blogs)."""

from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone

import feedparser

from buyingsignal.collectors.base import RawSignal, ingest
from buyingsignal.logging import get_logger

log = get_logger(__name__)

SOURCE = "rss"


def _entry_uid(entry, feed_url: str) -> str:
    # Prefer the feed-provided stable id; fall back to link, then title.
    return entry.get("id") or entry.get("link") or f"{feed_url}#{entry.get('title', '')}"


def _entry_published(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is None:
        return None
    return datetime.fromtimestamp(timegm(parsed), tz=timezone.utc)


def _entry_text(entry) -> str | None:
    if entry.get("content"):
        return entry["content"][0].get("value")
    return entry.get("summary")


def parse_feed(raw_bytes: bytes, feed_url: str) -> list[RawSignal]:
    """Parse fetched feed bytes into RawSignals (pure; unit-testable offline)."""
    parsed = feedparser.parse(raw_bytes)
    signals: list[RawSignal] = []
    for entry in parsed.entries:
        signals.append(
            RawSignal(
                source=SOURCE,
                source_uid=_entry_uid(entry, feed_url),
                url=entry.get("link"),
                title=entry.get("title"),
                text=_entry_text(entry),
                published_at=_entry_published(entry),
            )
        )
    return signals


async def collect_rss(ctx: dict) -> int:
    """Arq cron job: poll every configured RSS feed and ingest new entries."""
    settings = ctx["settings"]
    http = ctx["http"]
    total_new = 0

    for feed_url in settings.rss_feeds:
        try:
            resp = await http.get(feed_url, timeout=20.0)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — one bad feed must not kill the run
            log.warning("rss.fetch_failed", feed=feed_url, error=str(exc))
            continue
        signals = parse_feed(resp.content, feed_url)
        total_new += await ingest(ctx, signals)

    return total_new
