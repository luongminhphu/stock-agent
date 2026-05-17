"""
Schemas for SignalEngineAgent.

Owner: ai segment.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import Verdict, _coerce_confidence


class SignalUrgency(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Signal(BaseModel):
    """A single actionable signal from SignalEngineAgent."""

    ticker: str
    urgency: SignalUrgency
    verdict: Verdict
    thesis_aligned: bool = Field(
        description="True if signal aligns with active thesis for this ticker"
    )
    trigger_reason: str = Field(
        description="Why this signal was triggered - specific and actionable"
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description="Risk flags from watchdog/stress inputs",
    )
    action: str = Field(
        description="Recommended action: specific and time-bounded"
    )
    causal_sources: list[str] = Field(
        default_factory=list,
        description="Source agents/data contributing to this signal",
    )
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("risk_flags", "causal_sources", mode="before")
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]


class RankedSignal(Signal):
    """Signal with additional rank metadata from SignalEngineAgent."""

    rank_score: float = Field(
        default=0.0,
        description="Composite rank score: urgency weight x confidence. Set by engine post-processing.",
    )
    thesis_conflict_note: str = Field(
        default="",
        description=(
            "Non-empty when signal contradicts active thesis. "
            "E.g. 'Watchdog=BEARISH but thesis still ACTIVE - review assumptions'."
        ),
    )
    cross_signal_note: str = Field(
        default="",
        description=(
            "Cross-signal context: how this signal relates to other signals in same run."
        ),
    )
    feedback_note: str = Field(
        default="",
        description=(
            "Calibration note from FeedbackService. "
            "E.g. 'User ignored 3/3 Banking signals in past 30 days - lower priority'."
        ),
    )


class PortfolioRiskNote(BaseModel):
    """Rule-based portfolio risk context. Not AI-generated."""

    top_concentration: list[str] = Field(
        default_factory=list,
        description="Tickers with weight_pct > 25% - concentration risk.",
    )
    losing_positions: list[str] = Field(
        default_factory=list,
        description="Tickers with pnl_pct < -5%.",
    )
    misaligned_positions: list[str] = Field(
        default_factory=list,
        description="Tickers held but last_verdict is BEARISH.",
    )
    total_pnl_pct: float | None = Field(
        default=None,
        description="Total portfolio PnL %.",
    )
    position_count: int = Field(default=0)


class RiskAlert(BaseModel):
    """A cross-segment risk alert from SignalEngineAgent."""

    ticker: str
    alert_type: str = Field(
        description="Type of alert: THESIS_CONFLICT, CONCENTRATION, DRAWDOWN, SECTOR_ROTATION, etc."
    )
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    description: str
    source: str = Field(
        default="",
        description="Which agent/segment raised this alert",
    )


class OpportunityHint(BaseModel):
    """A short-term opportunity identified by SignalEngineAgent."""

    ticker: str
    opportunity_type: str = Field(
        description="Type: TECHNICAL_BREAKOUT, THESIS_CATALYST, SECTOR_MOMENTUM, etc."
    )
    time_horizon: str = Field(description="Estimated window: TODAY, THIS_WEEK, THIS_MONTH")
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


class SignalEngineOutput(BaseModel):
    """Structured output from SignalEngineAgent."""

    snapshot_date: str = Field(default="", description="Ng\u00e0y ch\u1ea1y signal engine, format YYYY-MM-DD")
    generated_at: str = Field(default="", description="ISO 8601 timestamp khi engine ch\u1ea1y.")
    signal_summary: str = Field(default="", description="1-line summary cho bot header.")
    portfolio_context: PortfolioRiskNote = Field(default_factory=PortfolioRiskNote)
    ranked_signals: list[RankedSignal] = Field(default_factory=list)
    thesis_review_triggers: list[str] = Field(default_factory=list)
    risk_alerts: list[RiskAlert] = Field(default_factory=list)
    opportunity_windows: list[OpportunityHint] = Field(default_factory=list)
    portfolio_concentration_note: str = Field(default="")
    confidence: float = Field(ge=0.0, le=1.0, description="\u0110\u1ed9 tin c\u1eady t\u1ed5ng th\u1ec3")
    reasoning_summary: str = Field(default="")

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator(
        "ranked_signals", "risk_alerts", "opportunity_windows", "thesis_review_triggers",
        mode="before",
    )
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]
