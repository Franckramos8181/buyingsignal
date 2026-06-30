"""One-shot CLI for local development and verification.

Runs a single collector (or the sweep) once against live sources, using the same
context wiring as the worker, then exits. Useful for the demo and for confirming
a source mapping before turning the standing loop on.

Examples:
    python -m buyingsignal.cli run-once --collector rss
    python -m buyingsignal.cli run-once --collector edgar
    python -m buyingsignal.cli score --signal-id 42
"""

from __future__ import annotations

import argparse
import asyncio

from arq import create_pool
from arq.connections import RedisSettings

from buyingsignal.collectors.edgar import collect_edgar
from buyingsignal.collectors.restapi import collect_rest
from buyingsignal.collectors.rss import collect_rss
from buyingsignal.config import get_settings
from buyingsignal.logging import configure_logging, get_logger
from buyingsignal.runner import setup_shared, teardown_shared
from buyingsignal.scoring.score import score_signal

log = get_logger(__name__)

_COLLECTORS = {
    "rss": collect_rss,
    "edgar": collect_edgar,
    "rest": collect_rest,
}


async def _build_ctx() -> dict:
    settings = get_settings()
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    ctx: dict = {"redis": redis}
    setup_shared(ctx)
    return ctx


async def _run_once(collector: str) -> None:
    ctx = await _build_ctx()
    try:
        new = await _COLLECTORS[collector](ctx)
        log.info("cli.run_once", collector=collector, new=new)
        print(f"{collector}: {new} new signal(s) ingested and enqueued")
    finally:
        await ctx["redis"].aclose()
        await teardown_shared(ctx)


async def _score_one(signal_id: int) -> None:
    ctx = await _build_ctx()
    try:
        result = await score_signal(ctx, signal_id)
        print(f"signal {signal_id}: {result}")
    finally:
        await ctx["redis"].aclose()
        await teardown_shared(ctx)


def main() -> None:
    configure_logging(get_settings().log_level)
    parser = argparse.ArgumentParser(prog="buyingsignal")
    sub = parser.add_subparsers(dest="command", required=True)

    p_once = sub.add_parser("run-once", help="Run a single collector cycle and exit")
    p_once.add_argument("--collector", required=True, choices=sorted(_COLLECTORS))

    p_score = sub.add_parser("score", help="Score a single signal by id")
    p_score.add_argument("--signal-id", type=int, required=True)

    args = parser.parse_args()
    if args.command == "run-once":
        asyncio.run(_run_once(args.collector))
    elif args.command == "score":
        asyncio.run(_score_one(args.signal_id))


if __name__ == "__main__":
    main()
