"""SEC EDGAR collector.

Polls EDGAR's public "current filings" Atom feed for selected form types. SEC
permits automated access provided requests carry a descriptive User-Agent with
contact info and stay within fair-access rate limits — both honored here. No
login, scraping of protected pages, or rate-limit circumvention is involved.
"""

from __future__ import annotations

import asyncio
import re
from calendar import timegm
from datetime import datetime, timezone

import feedparser
import httpx

from buyingsignal.collectors.base import RawSignal, ingest
from buyingsignal.logging import get_logger

log = get_logger(__name__)

SOURCE = "edgar"
_CURRENT_FEED = "https://www.sec.gov/cgi-bin/browse-edgar"

# Form types that tend to carry buying signals. 8-K = material events (incl.
# leadership/M&A/results), S-1/424B = capital raises/IPO, 425 = M&A comms.
DEFAULT_FORM_TYPES = ["8-K", "S-1", "424B5", "425"]

# SEC fair-access: keep well under 10 req/s. We add a polite gap between forms.
_POLITE_GAP_SECONDS = 0.5

# EDGAR entry titles look like: "8-K - ACME CORP (0001234567) (Filer)".
# Form types themselves contain hyphens (8-K, S-1), so split on the " - "
# separator (space-hyphen-space), not on any hyphen.
_TITLE_RE = re.compile(r"^(?P<form>.+?)\s+-\s+(?P<company>.+?)\s+\((?P<cik>\d+)\)")


def _parse_title(title: str) -> tuple[str | None, str | None]:
    m = _TITLE_RE.match(title or "")
    if not m:
        return None, None
    return m.group("company").strip(), m.group("form").strip()


def _entry_published(entry) -> datetime | None:
    parsed = entry.get("updated_parsed") or entry.get("published_parsed")
    if parsed is None:
        return None
    return datetime.fromtimestamp(timegm(parsed), tz=timezone.utc)


def parse_feed(raw_bytes: bytes, form_type: str) -> list[RawSignal]:
    """Parse an EDGAR Atom feed into RawSignals (pure; unit-testable offline)."""
    parsed = feedparser.parse(raw_bytes)
    signals: list[RawSignal] = []
    for entry in parsed.entries:
        company, form = _parse_title(entry.get("title", ""))
        signals.append(
            RawSignal(
                source=SOURCE,
                source_uid=entry.get("id") or entry.get("link", ""),
                url=entry.get("link"),
                company=company,
                title=entry.get("title"),
                text=entry.get("summary") or f"SEC {form or form_type} filing.",
                published_at=_entry_published(entry),
            )
        )
    return signals


async def _fetch_form(http: httpx.AsyncClient, user_agent: str, form_type: str) -> bytes:
    params = {
        "action": "getcurrent",
        "type": form_type,
        "company": "",
        "dateb": "",
        "owner": "include",
        "count": "40",
        "output": "atom",
    }
    resp = await http.get(
        _CURRENT_FEED,
        params=params,
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.content


async def collect_edgar(ctx: dict, form_types: list[str] | None = None) -> int:
    """Arq cron job: poll EDGAR current-filings feeds for the configured forms."""
    settings = ctx["settings"]
    http = ctx["http"]
    forms = form_types or DEFAULT_FORM_TYPES
    total_new = 0

    for form_type in forms:
        try:
            raw = await _fetch_form(http, settings.edgar_user_agent, form_type)
        except Exception as exc:  # noqa: BLE001 — one bad form must not kill the run
            log.warning("edgar.fetch_failed", form=form_type, error=str(exc))
            continue
        signals = parse_feed(raw, form_type)
        total_new += await ingest(ctx, signals)
        await asyncio.sleep(_POLITE_GAP_SECONDS)

    return total_new
