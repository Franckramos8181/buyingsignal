"""The scoring Arq job: raw signal -> LLM classification -> persist -> notify.

Designed to be idempotent across stages so Arq retries are safe:
  raw      -> score with the LLM, persist fields, advance to `scored`
  scored   -> notify Slack (if above threshold) and advance to `notified`,
              otherwise advance to `archived`
  terminal -> no-op
A Slack failure raises so Arq retries, but the (already persisted) score is not
recomputed on the next attempt.
"""

from __future__ import annotations

from buyingsignal.db import repo
from buyingsignal.db.models import SignalStatus
from buyingsignal.logging import get_logger
from buyingsignal.scoring.schema import ScoredSignal
from buyingsignal.sink import slack

log = get_logger(__name__)


async def score_signal(ctx: dict, signal_id: int) -> str:
    """Arq job entrypoint. Returns a short status string for observability."""
    settings = ctx["settings"]
    sessionmaker = ctx["sessionmaker"]
    scorer = ctx["scorer"]
    http = ctx["http"]

    async with sessionmaker() as session:
        signal = await repo.get_signal(session, signal_id)
        if signal is None:
            log.warning("score.missing", signal_id=signal_id)
            return "missing"

        if signal.status in (SignalStatus.notified.value, SignalStatus.archived.value):
            return "already_done"

        # --- stage 1: score (only when still raw) -------------------------
        if signal.status == SignalStatus.raw.value:
            scored: ScoredSignal = await scorer.score(
                title=signal.raw_title,
                body=signal.raw_text,
                source=signal.source,
            )
            await repo.persist_score(
                session,
                signal_id,
                event_type=scored.event_type.value,
                score=scored.relevance,
                summary=scored.summary,
                extracted=scored.key_fields,
                company=scored.company,
            )
            if settings.debug_log_payloads:
                log.info(
                    "score.scored",
                    signal_id=signal_id,
                    event_type=scored.event_type.value,
                    relevance=scored.relevance,
                )
            # Refresh local view for the notify stage.
            await session.refresh(signal)

        # --- stage 2: route ----------------------------------------------
        above_threshold = (
            signal.score is not None
            and signal.score >= settings.score_threshold
            and signal.event_type != "not_relevant"
        )
        if not above_threshold:
            await repo.mark_status(session, signal_id, SignalStatus.archived)
            return "archived"

        # --- stage 3: notify ---------------------------------------------
        scored_view = ScoredSignal(
            event_type=signal.event_type,  # type: ignore[arg-type]
            relevance=signal.score or 0,
            company=signal.company,
            summary=signal.summary or "",
            key_fields=signal.extracted or {},
        )
        ok = await slack.post_signal(
            http,
            settings.slack_webhook_url,
            scored_view,
            source_url=signal.url,
        )
        if not ok:
            # Leave at `scored`; raising makes Arq retry the notify stage only.
            raise RuntimeError(f"slack notify failed for signal {signal_id}")

        await repo.mark_status(session, signal_id, SignalStatus.notified)
        return "notified"
