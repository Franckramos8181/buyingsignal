"""Provider-abstracted LLM scoring client.

Groq, DeepInfra, Together and OpenRouter all expose an OpenAI-compatible
`/chat/completions` endpoint with tool calling, so a single httpx client covers
them and swapping provider/model (e.g. Groq Llama -> a DeepInfra Hermes model) is
purely configuration. We force a tool call whose arguments must validate against
`ScoredSignal`; on a validation miss we re-ask once before giving up.
"""

from __future__ import annotations

import asyncio
import json

import httpx
from pydantic import ValidationError

from buyingsignal.config import LLMProvider, Settings
from buyingsignal.logging import get_logger
from buyingsignal.scoring.prompts import build_messages
from buyingsignal.scoring.schema import EventType, ScoredSignal

log = get_logger(__name__)

# Default OpenAI-compatible base URLs per provider.
_PROVIDER_BASE_URLS = {
    LLMProvider.groq: "https://api.groq.com/openai/v1",
    LLMProvider.deepinfra: "https://api.deepinfra.com/v1/openai",
}

_TOOL_NAME = "record_signal"

# Hand-built tool schema (flat, enum inlined) for maximum cross-provider
# compatibility — some providers choke on pydantic's $ref/$defs output.
_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": "Record the structured classification of one buying signal.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "enum": [e.value for e in EventType],
                    "description": "Single best-fit buying-signal category.",
                },
                "relevance": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Strength as an outbound buying signal, 0-100.",
                },
                "company": {
                    "type": ["string", "null"],
                    "description": "Primary company the signal is about, if identifiable.",
                },
                "summary": {
                    "type": "string",
                    "description": "One- to two-sentence summary for a sales rep.",
                },
                "key_fields": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Outreach-useful extracted details, string values only.",
                },
            },
            "required": ["event_type", "relevance", "summary"],
        },
    },
}

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """Raised when scoring fails after retries — the Arq job should retry later."""


def _resolve_base_url(settings: Settings) -> str:
    if settings.llm_provider is LLMProvider.openai_compatible:
        if not settings.llm_base_url:
            raise LLMError("LLM_BASE_URL is required for openai-compatible provider")
        return settings.llm_base_url.rstrip("/")
    base = _PROVIDER_BASE_URLS.get(settings.llm_provider)
    if not base:
        raise LLMError(f"No base URL for provider {settings.llm_provider}")
    return settings.llm_base_url.rstrip("/") if settings.llm_base_url else base


class LLMScorer:
    """Thin scoring interface: `await scorer.score(...) -> ScoredSignal`."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self._settings = settings
        self._client = client
        self._base_url = _resolve_base_url(settings)
        self._model = settings.llm_model
        self._key = settings.active_llm_key

    async def _chat(self, messages: list[dict]) -> dict:
        if not self._key:
            raise LLMError("No LLM API key configured for the active provider")
        payload = {
            "model": self._model,
            "messages": messages,
            "tools": [_TOOL_SCHEMA],
            "tool_choice": {"type": "function", "function": {"name": _TOOL_NAME}},
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self._key}"}

        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                resp = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=30.0,
                )
                if resp.status_code in _RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError(
                        f"retryable {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_exc = exc
                await asyncio.sleep(min(2**attempt, 8))
        raise LLMError(f"chat request failed after retries: {last_exc}")

    @staticmethod
    def _extract_tool_args(response: dict) -> dict:
        try:
            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                raise LLMError("model did not return a tool call")
            raw_args = tool_calls[0]["function"]["arguments"]
            return json.loads(raw_args)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise LLMError(f"malformed tool-call response: {exc}") from exc

    async def score(self, *, title: str | None, body: str | None, source: str) -> ScoredSignal:
        """Classify one signal into a validated ScoredSignal, re-asking once on miss."""
        messages = build_messages(title, body, source)
        response = await self._chat(messages)
        args = self._extract_tool_args(response)
        try:
            return ScoredSignal.model_validate(args)
        except ValidationError as first_err:
            log.warning("llm.validation_retry", error=str(first_err))
            # Re-ask: feed back the prior (invalid) attempt and the error.
            retry_messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "Your previous answer failed validation with:\n"
                        f"{first_err}\n"
                        "Call record_signal again with corrected, schema-valid arguments."
                    ),
                }
            ]
            response = await self._chat(retry_messages)
            args = self._extract_tool_args(response)
            try:
                return ScoredSignal.model_validate(args)
            except ValidationError as second_err:
                raise LLMError(f"validation failed twice: {second_err}") from second_err
