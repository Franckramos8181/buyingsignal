"""Shared collector primitives: the RawSignal record and the ingest pipeline.

Every collector produces `RawSignal`s and hands them to `ingest`, which applies a
two-layer dedup (a fast Redis seen-key short-circuit, then the authoritative
DB UNIQUE constraint) and enqueues a scoring job for each genuinely new signal.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from buyingsignal.db import repo
from buyingsignal.logging import get_logger

log = get_logger(__name__)

# Redis short-circuit TTL: long enough to skip re-seen items between polls,
# short enough to self-heal. The DB constraint remains the source of truth.
_SEEN_TTL_SECONDS = 7 * 24 * 3600


class RawSignal(BaseModel):
    """The structured record every collector emits before scoring."""

    source: str
    source_uid: str  # stable per-source id used as the dedup natural key
    url: str | None = None
    company: str | None = None
    title: str | None = None
    text: str | None = None
    published_at: datetime | None = None


def _seen_key(source: str, uid: str) -> str:
    return f"seen:{source}:{uid}"


async def ingest(ctx: dict, signals: list[RawSignal]) -> int:
    """Persist new signals (deduped) and enqueue scoring. Returns count of new rows."""
    redis = ctx["redis"]
    sessionmaker = ctx["sessionmaker"]
    new_count = 0

    for sig in signals:
        # Layer 1: Redis NX short-circuit to avoid hammering the DB on re-polls.
        key = _seen_key(sig.source, sig.source_uid)
        is_new_in_cache = await redis.set(key, "1", ex=_SEEN_TTL_SECONDS, nx=True)
        if not is_new_in_cache:
            continue

        # Layer 2: authoritative DB dedup via UNIQUE(source, source_uid).
        async with sessionmaker() as session:
            signal_id = await repo.insert_raw(
                session,
                source=sig.source,
                source_uid=sig.source_uid,
                url=sig.url,
                company=sig.company,
                raw_title=sig.title,
                raw_text=sig.text,
                published_at=sig.published_at,
            )

        if signal_id is None:
            # Seen in DB but not cache (e.g. cache flushed) — nothing to do.
            continue

        await redis.enqueue_job("score_signal", signal_id)
        new_count += 1

    if new_count:
        log.info("collector.ingested", source=signals[0].source, new=new_count)
    return new_count
