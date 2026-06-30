"""Arq worker definition.

Two responsibilities run in one process:

  * **Scoring** is a durable Arq job (`score_signal`) enqueued by collectors and
    by the cron backstop. Jobs live in Redis, so they survive worker restarts.
  * **Collection** runs as event-loop background tasks, each polling its source
    on a configurable interval and enqueuing scoring jobs for new signals. This
    is the "event loop + durable queue" shape: polling is best-effort and simply
    resumes on restart, while the work it produces is durable.

A periodic cron job also sweeps for any raw rows whose scoring job was lost, so
no signal is stranded.
"""

from __future__ import annotations

import asyncio

from arq import cron
from arq.connections import RedisSettings

from buyingsignal.collectors.edgar import collect_edgar
from buyingsignal.collectors.restapi import collect_rest
from buyingsignal.collectors.rss import collect_rss
from buyingsignal.config import get_settings
from buyingsignal.db import repo
from buyingsignal.logging import configure_logging, get_logger
from buyingsignal.runner import setup_shared, teardown_shared
from buyingsignal.scoring.score import score_signal

log = get_logger(__name__)


async def _poll_loop(ctx: dict, name: str, fn, interval: int) -> None:
    """Run a collector forever on a fixed interval, isolating failures."""
    if interval <= 0:
        return
    log.info("collector.loop_start", collector=name, interval=interval)
    while True:
        try:
            new = await fn(ctx)
            log.info("collector.cycle", collector=name, new=new)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — keep the loop alive
            log.error("collector.cycle_failed", collector=name, error=str(exc))
        await asyncio.sleep(interval)


async def sweep_unscored(ctx: dict) -> int:
    """Cron backstop: re-enqueue scoring for any raw rows that slipped through."""
    redis = ctx["redis"]
    sessionmaker = ctx["sessionmaker"]
    async with sessionmaker() as session:
        rows = await repo.fetch_unscored(session, limit=200)
    for row in rows:
        await redis.enqueue_job("score_signal", row.id)
    if rows:
        log.info("sweep.requeued", count=len(rows))
    return len(rows)


async def on_startup(ctx: dict) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    setup_shared(ctx)

    # Launch collector poll loops as background tasks.
    tasks: list[asyncio.Task] = []
    tasks.append(
        asyncio.create_task(
            _poll_loop(ctx, "rss", collect_rss, settings.rss_interval_seconds)
        )
    )
    tasks.append(
        asyncio.create_task(
            _poll_loop(ctx, "edgar", collect_edgar, settings.edgar_interval_seconds)
        )
    )
    tasks.append(
        asyncio.create_task(
            _poll_loop(ctx, "rest", collect_rest, settings.rest_interval_seconds)
        )
    )
    ctx["_poll_tasks"] = tasks
    log.info("worker.started", provider=settings.llm_provider.value, model=settings.llm_model)


async def on_shutdown(ctx: dict) -> None:
    for task in ctx.get("_poll_tasks", []):
        task.cancel()
    for task in ctx.get("_poll_tasks", []):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await teardown_shared(ctx)
    log.info("worker.stopped")


class WorkerSettings:
    """Discovered by `arq buyingsignal.worker.WorkerSettings`."""

    functions = [score_signal]
    cron_jobs = [
        # Backstop sweep every 5 minutes for stranded raw rows.
        cron(sweep_unscored, minute=set(range(0, 60, 5)), run_at_startup=False),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    max_tries = 4
    job_timeout = 120

    @staticmethod
    def redis_settings() -> RedisSettings:
        return RedisSettings.from_dsn(get_settings().redis_url)


# Arq reads `redis_settings` as an attribute; bind the resolved value.
WorkerSettings.redis_settings = WorkerSettings.redis_settings()


def main() -> None:
    """Console-script entrypoint (`buyingsignal-worker`)."""
    from arq.worker import run_worker

    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()
