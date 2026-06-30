"""The LLM output contract.

`ScoredSignal` is both the JSON schema we hand the model (function/tool args) and
the pydantic model we validate the response against. If the model returns
anything that does not validate, we reject and re-ask once (see `llm.py`).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """B2B buying-signal taxonomy. `not_relevant` lets the model decline cleanly."""

    funding_round = "funding_round"
    acquisition = "acquisition"
    leadership_change = "leadership_change"
    expansion = "expansion"  # new office, market entry, hiring surge
    product_launch = "product_launch"
    earnings_or_guidance = "earnings_or_guidance"
    partnership = "partnership"
    layoffs_or_restructuring = "layoffs_or_restructuring"
    regulatory_or_legal = "regulatory_or_legal"
    other_relevant = "other_relevant"
    not_relevant = "not_relevant"


class ScoredSignal(BaseModel):
    """Structured classification of a single raw signal."""

    event_type: EventType = Field(
        description="The single best-fit buying-signal category for this item."
    )
    relevance: int = Field(
        ge=0,
        le=100,
        description=(
            "How strong a B2B outbound buying signal this is, 0-100. "
            "Use <40 for weak/none, 40-69 moderate, 70+ strong."
        ),
    )
    company: str | None = Field(
        default=None,
        description="Primary company the signal is about, if identifiable.",
    )
    summary: str = Field(
        description="One- to two-sentence plain summary of the signal for a sales rep."
    )
    key_fields: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Extracted structured details relevant to outreach, e.g. "
            "amount, round, role, location, product. String values only."
        ),
    )

    @property
    def is_relevant(self) -> bool:
        return self.event_type is not EventType.not_relevant
