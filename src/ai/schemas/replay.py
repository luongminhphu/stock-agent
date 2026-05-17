"""
Schemas for ReplayAgent — post-mortem of a past decision.

Owner: ai segment.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import _coerce_confidence


class OutcomeVerdict(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"
    BREAK_EVEN = "BREAK_EVEN"
    PENDING = "PENDING"


class ReplayOutput(BaseModel):
    """Structured output from ReplayAgent — post-mortem of a past decision."""

    ticker: str
    decision_date: str
    original_action: str
    outcome_verdict: OutcomeVerdict
    outcome_pnl_pct: float | None = None
    what_went_right: list[str] = Field(default_factory=list)
    what_went_wrong: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(
        default_factory=list,
        description="Actionable lessons for future decisions",
    )
    thesis_accuracy_note: str = Field(
        default="",
        description="How accurate was the original thesis?",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("what_went_right", "what_went_wrong", "lessons", mode="before")
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]
