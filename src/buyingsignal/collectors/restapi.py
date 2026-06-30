"""Generic collector for permitted public JSON REST APIs.

Sources are declared in config (REST_SOURCES) as `RestSource` records, so adding
a new permitted API is configuration, not code. Each source describes where the
list of records lives and which fields map onto a RawSignal.
"""

from __future__ import annotations

from typing import Any

from buyingsignal.collectors.base import RawSignal, ingest
from buyingsignal.config import RestSource
from buyingsignal.logging import get_logger

log = get_logger(__name__)


def _walk(data: Any, dot_path: str) -> Any:
    """Resolve a dot path within a nested dict; '' returns data unchanged."""
    if not dot_path:
        return data
    for part in dot_path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


def parse_payload(payload: Any, source: RestSource) -> list[RawSignal]:
    """Map a JSON payload into RawSignals per the source's field mapping."""
    items = _walk(payload, source.items_path)
    if not isinstance(items, list):
        log.warning("restapi.no_list", source=source.name, items_path=source.items_path)
        return []

    signals: list[RawSignal] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        uid = item.get(source.uid_field)
        if uid is None:
            continue
        text = item.get(source.text_field) if source.text_field else None
        signals.append(
            RawSignal(
                source=f"rest:{source.name}",
                source_uid=str(uid),
                url=item.get(source.url_field),
                title=item.get(source.title_field),
                text=text,
            )
        )
    return signals


async def collect_rest(ctx: dict) -> int:
    """Arq cron job: poll every configured permitted REST source."""
    settings = ctx["settings"]
    http = ctx["http"]
    total_new = 0

    for source in settings.rest_sources:
        try:
            resp = await http.get(source.url, headers=source.headers, timeout=20.0)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 — isolate per-source failures
            log.warning("restapi.fetch_failed", source=source.name, error=str(exc))
            continue
        signals = parse_payload(payload, source)
        total_new += await ingest(ctx, signals)

    return total_new
