"""Tín hiệu đã xếp hạng + cảnh báo rủi ro — input tuỳ chọn của PortfolioRiskNarrator/BriefingAgent.

Owner: ai segment. Tách từ schemas/signal_engine.py khi gỡ SignalEngineAgent; giữ nguyên
contract để các agent còn dùng không đổi.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import Verdict, _coerce_confidence

__all__ = ["SignalUrgency", "Signal", "RankedSignal", "RiskAlert"]


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
    action: str = Field(description="Recommended action: specific and time-bounded")
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
        return v


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
            "E.g. 'Plan light=do but thesis still ACTIVE - review assumptions'."
        ),
    )
    cross_signal_note: str = Field(
        default="",
        description=("Cross-signal context: how this signal relates to other signals in same run."),
    )
    feedback_note: str = Field(
        default="",
        description=(
            "Calibration note from FeedbackService. "
            "E.g. 'User ignored 3/3 Banking signals in past 30 days - lower priority'."
        ),
    )


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
