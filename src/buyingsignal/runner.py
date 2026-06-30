"""Shared runtime context construction for the worker and the one-shot CLI.

Both the long-running Arq worker and the `run-once` CLI need the same set of
resources wired into the job `ctx`: settings, an httpx client, a DB session
factory and an LLM scorer. This module centralizes that so the two entrypoints
cannot drift.
"""

from __future__ import annotations

import httpx

from buyingsignal.config import Settings, get_settings
from buyingsignal.db.engine import dispose_engine, get_sessionmaker
from buyingsignal.scoring.llm import LLMScorer

# A shared, polite default User-Agent for non-EDGAR fetches.
_DEFAULT_UA = "buyingsignal/0.1 (+https://example.com/bot)"


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": _DEFAULT_UA},
        timeout=httpx.Timeout(20.0, connect=10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        follow_redirects=True,
    )


def setup_shared(ctx: dict) -> dict:
    """Populate shared resources into a job context (redis must already be set)."""
    settings = get_settings()
    http = build_http_client(settings)
    ctx["settings"] = settings
    ctx["http"] = http
    ctx["sessionmaker"] = get_sessionmaker()
    ctx["scorer"] = LLMScorer(settings, http)
    return ctx


async def teardown_shared(ctx: dict) -> None:
    http = ctx.get("http")
    if http is not None:
        await http.aclose()
    await dispose_engine()
