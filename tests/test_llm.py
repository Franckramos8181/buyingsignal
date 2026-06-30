"""LLM scorer: tool-call parsing, forced tool choice, and one-shot re-ask.

Uses httpx.MockTransport so no network or API key is needed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from buyingsignal.config import LLMProvider, Settings
from buyingsignal.scoring.llm import LLMError, LLMScorer


def _tool_response(args: dict) -> httpx.Response:
    body = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "record_signal",
                                "arguments": json.dumps(args),
                            }
                        }
                    ]
                }
            }
        ]
    }
    return httpx.Response(200, json=body)


def _settings() -> Settings:
    return Settings(llm_provider=LLMProvider.groq, groq_api_key="test-key")


async def test_happy_path_parses_tool_args():
    valid = {
        "event_type": "funding_round",
        "relevance": 80,
        "company": "Acme",
        "summary": "Acme raised a round.",
        "key_fields": {"amount": "$20M"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        # Verify we force the tool call and send a bearer token.
        payload = json.loads(request.content)
        assert payload["tool_choice"]["function"]["name"] == "record_signal"
        assert request.headers["Authorization"].startswith("Bearer ")
        return _tool_response(valid)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    scorer = LLMScorer(_settings(), client)
    result = await scorer.score(title="Acme funding", body="text", source="rss")
    assert result.relevance == 80
    assert result.company == "Acme"
    await client.aclose()


async def test_reask_on_invalid_then_valid():
    calls = {"n": 0}
    invalid = {"event_type": "funding_round", "relevance": 999, "summary": "x"}
    valid = {"event_type": "funding_round", "relevance": 70, "summary": "ok"}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _tool_response(invalid if calls["n"] == 1 else valid)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    scorer = LLMScorer(_settings(), client)
    result = await scorer.score(title="t", body="b", source="rss")
    assert calls["n"] == 2  # re-asked exactly once
    assert result.relevance == 70
    await client.aclose()


async def test_raises_after_two_invalid():
    invalid = {"event_type": "funding_round", "relevance": 999, "summary": "x"}

    def handler(_request: httpx.Request) -> httpx.Response:
        return _tool_response(invalid)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    scorer = LLMScorer(_settings(), client)
    with pytest.raises(LLMError):
        await scorer.score(title="t", body="b", source="rss")
    await client.aclose()
