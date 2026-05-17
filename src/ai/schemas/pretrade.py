"""
Schemas for PreTradeAgent.

Owner: ai segment.
Triggered by: manual /pretrade command before placing an order.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import Verdict, _coerce_confidence


class TradeDecision(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    REDUCE = "REDUCE"
    HOLD = "HOLD"


class AlignmentStatus(StrEnum):
    ALIGNED = "ALIGNED"
    NEUTRAL = "NEUTRAL"
    MISALIGNED = "MISALIGNED"


class ResolutionCategory(StrEnum):
    THESIS_CONFLICT = "THESIS_CONFLICT"
    RISK_LIMIT = "RISK_LIMIT"
    TIMING = "TIMING"
    MARKET_CONDITION = "MARKET_CONDITION"
    PORTFOLIO_BALANCE = "PORTFOLIO_BALANCE"


class ResolutionStep(BaseModel):
    """A single resolution step for a pre-trade conflict."""

    category: ResolutionCategory
    issue: str = Field(description="Specific issue identified")
    resolution: str = Field(description="Recommended resolution")
    priority: Literal["BLOCKING", "HIGH", "MEDIUM", "LOW"] = Field(
        default="MEDIUM",
        description="Priority of resolving this issue before trading",
    )


class PreTradeCheckOutput(BaseModel):
    """Structured output from PreTradeAgent.

    Owner: ai segment.
    Triggered by: manual /pretrade command before placing an order.
    """

    ticker: str
    intended_action: TradeDecision
    alignment: AlignmentStatus = Field(
        description="How well the trade aligns with existing thesis and strategy"
    )
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    proceed_recommendation: bool = Field(
        description="True if AI recommends proceeding with the trade"
    )
    blocking_issues: list[str] = Field(
        default_factory=list,
        description="Issues that must be resolved before trading",
    )
    resolution_steps: list[ResolutionStep] = Field(
        default_factory=list,
        description="Ordered steps to resolve conflicts",
    )
    risk_summary: str = Field(description="Brief risk assessment for this specific trade")
    thesis_alignment_note: str = Field(
        default="",
        description="How this trade relates to the existing thesis",
    )
    sizing_note: str = Field(
        default="",
        description="Position sizing guidance if applicable",
    )
    confidence_explanation: str = Field(
        default="",
        description="Why confidence is at this level",
    )
    summary: str = Field(description="2-3 sentence overall summary")

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("blocking_issues", "resolution_steps", mode="before")
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]
