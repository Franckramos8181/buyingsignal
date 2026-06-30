"""Data-access helpers. All dedup/idempotency rules live here, not in callers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from buyingsignal.db.models import Signal, SignalStatus


async def insert_raw(
    session: AsyncSession,
    *,
    source: str,
    source_uid: str,
    url: str | None = None,
    company: str | None = None,
    raw_title: str | None = None,
    raw_text: str | None = None,
    published_at: datetime | None = None,
) -> int | None:
    """Insert a raw signal, deduped on (source, source_uid).

    Returns the new row id when this was a genuinely new signal, or None when an
    identical signal already existed (ON CONFLICT DO NOTHING). Callers should
    only enqueue scoring when a real id comes back, making re-polling idempotent.
    """
    stmt = (
        pg_insert(Signal)
        .values(
            source=source,
            source_uid=source_uid,
            url=url,
            company=company,
            raw_title=raw_title,
            raw_text=raw_text,
            published_at=published_at,
            status=SignalStatus.raw.value,
        )
        .on_conflict_do_nothing(constraint="uq_signals_source_uid")
        .returning(Signal.id)
    )
    result = await session.execute(stmt)
    row = result.first()
    await session.commit()
    return row[0] if row else None


async def get_signal(session: AsyncSession, signal_id: int) -> Signal | None:
    return await session.get(Signal, signal_id)


async def fetch_unscored(session: AsyncSession, limit: int = 100) -> list[Signal]:
    """Backstop sweep for raw rows whose scoring job was lost (e.g. crash)."""
    stmt = (
        select(Signal)
        .where(Signal.status == SignalStatus.raw.value)
        .order_by(Signal.created_at)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def persist_score(
    session: AsyncSession,
    signal_id: int,
    *,
    event_type: str,
    score: int,
    summary: str,
    extracted: dict,
    company: str | None,
) -> None:
    """Persist LLM-derived fields and stage the row to `scored`.

    The notify/archive transition is applied separately (see `mark_status`) so a
    Slack failure can be retried without re-running the LLM.
    """
    values: dict = {
        "event_type": event_type,
        "score": score,
        "summary": summary,
        "extracted": extracted,
        "status": SignalStatus.scored.value,
        "scored_at": datetime.now(timezone.utc),
    }
    if company:
        values["company"] = company
    await session.execute(
        update(Signal).where(Signal.id == signal_id).values(**values)
    )
    await session.commit()


async def mark_status(
    session: AsyncSession, signal_id: int, status: SignalStatus
) -> None:
    await session.execute(
        update(Signal).where(Signal.id == signal_id).values(status=status.value)
    )
    await session.commit()
