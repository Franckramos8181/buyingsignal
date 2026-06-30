"""Ingest dedup orchestration: only genuinely new signals get enqueued.

Exercises the two-layer dedup in `collectors.base.ingest` with in-memory fakes
for Redis and the DB, so it runs offline. The authoritative ON CONFLICT behavior
is covered by the live end-to-end run documented in the README.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from buyingsignal.collectors import base
from buyingsignal.collectors.base import RawSignal, ingest


class FakeRedis:
    """Models SET NX (seen-key) + enqueue_job."""

    def __init__(self):
        self._keys: set[str] = set()
        self.enqueued: list[int] = []

    async def set(self, name, value, ex=None, nx=False):  # noqa: ARG002
        if nx and name in self._keys:
            return None
        self._keys.add(name)
        return True

    async def enqueue_job(self, _fn, signal_id):
        self.enqueued.append(signal_id)


class FakeDB:
    """Models the UNIQUE(source, source_uid) constraint via a seen set."""

    def __init__(self):
        self._seen: set[tuple[str, str]] = set()
        self._next_id = 1

    def insert(self, source, source_uid):
        key = (source, source_uid)
        if key in self._seen:
            return None  # ON CONFLICT DO NOTHING
        self._seen.add(key)
        new_id = self._next_id
        self._next_id += 1
        return new_id


@pytest.fixture
def wired(monkeypatch):
    redis = FakeRedis()
    db = FakeDB()

    @asynccontextmanager
    async def fake_session():
        yield object()

    def sessionmaker():
        return fake_session()

    async def fake_insert_raw(_session, *, source, source_uid, **_kwargs):
        return db.insert(source, source_uid)

    monkeypatch.setattr(base.repo, "insert_raw", fake_insert_raw)
    ctx = {"redis": redis, "sessionmaker": sessionmaker}
    return ctx, redis, db


def _sig(uid: str) -> RawSignal:
    return RawSignal(source="rss", source_uid=uid, title="t", text="b")


async def test_new_signals_enqueue_once(wired):
    ctx, redis, _ = wired
    new = await ingest(ctx, [_sig("a"), _sig("b")])
    assert new == 2
    assert redis.enqueued == [1, 2]


async def test_repoll_does_not_reenqueue(wired):
    ctx, redis, _ = wired
    await ingest(ctx, [_sig("a"), _sig("b")])
    # Same items again: Redis NX short-circuit prevents re-enqueue.
    new = await ingest(ctx, [_sig("a"), _sig("b")])
    assert new == 0
    assert redis.enqueued == [1, 2]


async def test_db_conflict_when_cache_missed(wired):
    ctx, redis, db = wired
    await ingest(ctx, [_sig("a")])
    # Simulate cache flush: DB still has the row, so insert returns None.
    redis._keys.clear()
    new = await ingest(ctx, [_sig("a")])
    assert new == 0
    assert redis.enqueued == [1]  # no second enqueue
