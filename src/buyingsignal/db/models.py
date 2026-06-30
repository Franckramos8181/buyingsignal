"""Database schema.

A single `signals` table holds a record through its whole lifecycle, tracked by
`status`: a collector inserts it as `raw`, the scoring job fills the LLM-derived
fields and moves it to `scored`, and the Slack notifier moves it to `notified`.

Dedup is enforced at the DB with UNIQUE(source, source_uid); see `repo.insert_raw`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SignalStatus(str, Enum):
    raw = "raw"
    scored = "scored"
    notified = "notified"
    # Terminal: scored below threshold; kept for analytics, not notified.
    archived = "archived"


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        # Natural key: a given source's own stable id for the event.
        UniqueConstraint("source", "source_uid", name="uq_signals_source_uid"),
        Index("ix_signals_status", "status"),
        Index("ix_signals_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # --- raw fields (set by collector) ------------------------------------
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uid: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    company: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- scored fields (set by scoring job) -------------------------------
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # --- lifecycle --------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SignalStatus.raw.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    scored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
