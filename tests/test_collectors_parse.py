"""Pure parsing tests for the RSS, EDGAR and REST collectors (offline)."""

from __future__ import annotations

from pathlib import Path

from buyingsignal.collectors import edgar, rss
from buyingsignal.collectors.restapi import parse_payload
from buyingsignal.config import RestSource

FIXTURES = Path(__file__).parent / "fixtures"


def test_rss_parse_maps_entries():
    raw = (FIXTURES / "sample_rss.xml").read_bytes()
    signals = rss.parse_feed(raw, "https://example.com/feed")
    assert len(signals) == 2
    first = signals[0]
    assert first.source == "rss"
    assert first.source_uid == "acme-series-b-0001"
    assert first.url == "https://example.com/news/acme-series-b"
    assert "Series B" in first.title
    assert first.published_at is not None
    assert first.published_at.year == 2025


def test_edgar_parse_extracts_company_and_uid():
    raw = (FIXTURES / "sample_edgar_atom.xml").read_bytes()
    signals = edgar.parse_feed(raw, "8-K")
    assert len(signals) == 2
    acme = signals[0]
    assert acme.source == "edgar"
    assert acme.company == "ACME CORP"
    assert "0001234567-25-000001" in acme.source_uid
    assert acme.url.startswith("https://www.sec.gov/")


def test_rest_parse_walks_items_path():
    source = RestSource(
        name="demo",
        url="https://api.example.com/events",
        items_path="data.items",
        uid_field="id",
        title_field="headline",
        url_field="link",
        text_field="body",
    )
    payload = {
        "data": {
            "items": [
                {
                    "id": 7,
                    "headline": "Initech acquires Hooli",
                    "link": "https://x/7",
                    "body": "Deal closed.",
                },
                {"id": 8, "headline": "No link item"},
            ]
        }
    }
    signals = parse_payload(payload, source)
    assert len(signals) == 2
    assert signals[0].source == "rest:demo"
    assert signals[0].source_uid == "7"
    assert signals[0].title == "Initech acquires Hooli"
    assert signals[0].text == "Deal closed."
    # Missing optional fields are tolerated.
    assert signals[1].source_uid == "8"
    assert signals[1].url is None


def test_rest_parse_handles_non_list():
    source = RestSource(name="bad", url="https://x", items_path="missing")
    assert parse_payload({"data": 1}, source) == []
