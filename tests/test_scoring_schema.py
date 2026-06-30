"""ScoredSignal contract: accepts valid LLM tool args, rejects malformed ones."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from buyingsignal.scoring.schema import EventType, ScoredSignal


def test_valid_tool_args_parse():
    args = {
        "event_type": "funding_round",
        "relevance": 82,
        "company": "Acme Corp",
        "summary": "Acme raised a $20M Series B led by Foo Ventures.",
        "key_fields": {"amount": "$20M", "round": "Series B", "lead": "Foo Ventures"},
    }
    scored = ScoredSignal.model_validate(args)
    assert scored.event_type is EventType.funding_round
    assert scored.relevance == 82
    assert scored.is_relevant is True


def test_defaults_for_optional_fields():
    scored = ScoredSignal.model_validate(
        {"event_type": "not_relevant", "relevance": 5, "summary": "Routine update."}
    )
    assert scored.company is None
    assert scored.key_fields == {}
    assert scored.is_relevant is False


def test_relevance_out_of_range_rejected():
    with pytest.raises(ValidationError):
        ScoredSignal.model_validate(
            {"event_type": "funding_round", "relevance": 250, "summary": "x"}
        )


def test_unknown_event_type_rejected():
    with pytest.raises(ValidationError):
        ScoredSignal.model_validate(
            {"event_type": "made_up_category", "relevance": 50, "summary": "x"}
        )


def test_missing_required_summary_rejected():
    with pytest.raises(ValidationError):
        ScoredSignal.model_validate({"event_type": "funding_round", "relevance": 50})
