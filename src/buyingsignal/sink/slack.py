"""Slack notification sink — posts a compact review card via an incoming webhook."""

from __future__ import annotations

import httpx

from buyingsignal.logging import get_logger
from buyingsignal.scoring.schema import ScoredSignal

log = get_logger(__name__)


def build_blocks(scored: ScoredSignal, *, url: str | None) -> list[dict]:
    """Build Slack Block Kit blocks for a scored signal review card."""
    company = scored.company or "Unknown company"
    header = f"{company} — {scored.event_type.value} ({scored.relevance})"
    fields = [
        {"type": "mrkdwn", "text": f"*Event:*\n{scored.event_type.value}"},
        {"type": "mrkdwn", "text": f"*Score:*\n{scored.relevance}/100"},
    ]
    for key, value in list(scored.key_fields.items())[:6]:
        fields.append({"type": "mrkdwn", "text": f"*{key}:*\n{value}"})

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": scored.summary[:2900]}},
        {"type": "section", "fields": fields},
    ]
    if url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open source"},
                        "url": url,
                    }
                ],
            }
        )
    return blocks


async def post_signal(
    client: httpx.AsyncClient,
    webhook_url: str,
    scored: ScoredSignal,
    *,
    source_url: str | None,
) -> bool:
    """Post a review card to Slack. Returns True on success."""
    if not webhook_url:
        log.warning("slack.skip", reason="no_webhook_configured")
        return False

    payload = {
        "text": f"{scored.company or 'Signal'}: {scored.event_type.value} ({scored.relevance})",
        "blocks": build_blocks(scored, url=source_url),
    }
    try:
        resp = await client.post(webhook_url, json=payload, timeout=10.0)
        resp.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        # Never log the webhook URL itself (it is a secret).
        log.error("slack.post_failed", error=str(exc))
        return False
