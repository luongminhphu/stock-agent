"""
Schemas for SignalCredibilityAgent.

Owner: ai segment.
Input: raw signal report + historical context.
Output feeds: watchlist (alert filtering), signal_engine (credibility weight).
"""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import _coerce_confidence


class SignalVerdict(StrEnum):
    CREDIBLE = "CREDIBLE"
    WEAK = "WEAK"
    NOISE = "NOISE"
    CONFLICTING = "CONFLICTING"


class SignalCredibilityOutput(BaseModel):
    """Structured output from SignalCredibilityAgent.

    Owner: ai segment.
    Input: raw signal report + historical context.
    Output feeds: watchlist (alert filtering), signal_engine (credibility weight).
    """

    ticker: str
    signal_type: str
    verdict: SignalVerdict
    credibility_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Credibility score 0-1",
    )
    supporting_factors: list[str] = Field(
        default_factory=list,
        description="Factors supporting signal credibility",
    )
    contra_factors: list[str] = Field(
        default_factory=list,
        description="Factors reducing signal credibility",
    )
    recommended_weight: float = Field(
        ge=0.0,
        le=1.0,
        description="Recommended weight to apply when using this signal",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str

    @field_validator("credibility_score", "recommended_weight", "confidence", mode="before")
    @classmethod
    def coerce_floats(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("supporting_factors", "contra_factors", mode="before")
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]
