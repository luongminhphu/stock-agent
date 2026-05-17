"""
Schemas for WatchdogAgent.

Owner: ai segment.
Input: thesis assumptions + live market data snapshot.
Output feeds: signal_engine (as watchdog_outputs), briefing (alert context).
"""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import Verdict, _coerce_confidence


class OverallHealth(StrEnum):
    HEALTHY = "HEALTHY"
    WATCH = "WATCH"
    CONCERN = "CONCERN"
    CRITICAL = "CRITICAL"


class WatchdogThreatLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class WatchdogRecommendedAction(StrEnum):
    HOLD = "HOLD"
    MONITOR = "MONITOR"
    REVIEW_THESIS = "REVIEW_THESIS"
    REDUCE = "REDUCE"
    EXIT = "EXIT"


class ThreatenedAssumptionWatchdog(BaseModel):
    """A thesis assumption under threat — watchdog variant."""

    assumption_text: str
    threat_level: WatchdogThreatLevel
    evidence: str


class WatchdogOutput(BaseModel):
    """Structured output from WatchdogAgent.

    Owner: ai segment.
    Input: thesis assumptions + live market data snapshot.
    Output feeds: signal_engine (as watchdog_outputs), briefing (alert context).
    """

    ticker: str
    overall_health: OverallHealth
    verdict: Verdict
    health_score: int = Field(
        ge=0,
        le=100,
        description="Composite health score 0-100",
    )
    threatened_assumptions: list[ThreatenedAssumptionWatchdog] = Field(
        default_factory=list
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description="Specific risk flags identified",
    )
    recommended_action: WatchdogRecommendedAction
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("threatened_assumptions", "risk_flags", mode="before")
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]
