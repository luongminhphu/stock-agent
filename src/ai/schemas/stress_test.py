"""
Schemas for StressTestAgent.

Owner: ai segment.
Input: thesis assumptions + stress scenario description.
Output feeds: signal_engine (as stress_outputs), briefing (risk context).
"""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import _coerce_confidence


class ThreatLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ThreatenedAssumption(BaseModel):
    """A thesis assumption threatened by a stress scenario."""

    assumption_text: str
    threat_level: ThreatLevel
    explanation: str
    probability_of_invalidation: float = Field(
        ge=0.0,
        le=1.0,
        description="Estimated probability assumption becomes invalid under scenario",
    )

    @field_validator("probability_of_invalidation", mode="before")
    @classmethod
    def coerce_prob(cls, v: object) -> float:
        return _coerce_confidence(v)


class StressTestOutput(BaseModel):
    """Structured output from StressTestAgent.

    Owner: ai segment.
    Input: thesis assumptions + stress scenario description.
    Output feeds: signal_engine (as stress_outputs), briefing (risk context).
    """

    ticker: str
    scenario: str = Field(description="Name/description of the stress scenario tested")
    overall_threat: ThreatLevel
    threatened_assumptions: list[ThreatenedAssumption] = Field(
        default_factory=list
    )
    portfolio_impact_note: str = Field(
        default="",
        description="How this scenario would impact overall portfolio if it materialises",
    )
    hedge_suggestions: list[str] = Field(
        default_factory=list,
        description="Potential hedging actions to mitigate scenario risk",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("threatened_assumptions", "hedge_suggestions", mode="before")
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]
