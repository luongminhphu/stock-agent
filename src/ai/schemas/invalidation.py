"""Schema for ThesisInvalidationDetector output.

Owner: ai segment.
Caller: thesis.invalidation_service (passes InvalidationCheckResult → agent
        → InvalidationSignal for alert / watchlist flag / optional DB write).

Design note:
  InvalidationService already owns the rule-based breach logic
  (stop_loss breach, assumption ratio). This schema captures the AI layer
  on top: verdict confirmation, breach narrative, and recommended action
  ready for bot alert or watchlist flag.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import _coerce_confidence


class BreachType(StrEnum):
    """Primary breach type that triggered the invalidation check.

    STOP_LOSS         — current price ≤ stop_loss threshold.
    ASSUMPTION_RATIO  — >50% of assumptions flipped to INVALID.
    CATALYST_EXPIRED  — key catalyst expired without triggering.
    WATCHDOG_CRITICAL — BEARISH + CRITICAL from WatchdogService.
    COMPOSITE         — multiple breach types active simultaneously.
    """

    STOP_LOSS = "STOP_LOSS"
    ASSUMPTION_RATIO = "ASSUMPTION_RATIO"
    CATALYST_EXPIRED = "CATALYST_EXPIRED"
    WATCHDOG_CRITICAL = "WATCHDOG_CRITICAL"
    COMPOSITE = "COMPOSITE"


class InvalidationVerdict(StrEnum):
    """AI confirmation verdict.

    CONFIRMED  — AI agrees thesis is invalidated; recommend action.
    SUSPECTED  — breach detected but context is ambiguous; escalate to review.
    CLEARED    — rule triggered but AI sees mitigating factors; no action yet.
    """

    CONFIRMED = "CONFIRMED"
    SUSPECTED = "SUSPECTED"
    CLEARED = "CLEARED"


class InvalidationSignal(BaseModel):
    """Structured output from ThesisInvalidationDetector.

    Downstream consumers:
      - bot: format Discord alert with breach_type + narrative + action.
      - watchlist: flag thesis for immediate attention.
      - thesis.invalidation_service: decide whether to call
        ThesisService.mark_invalidated() based on verdict.
      - (optional) ThesisJudgeAgent: pass as signal_context when verdict
        is SUSPECTED to get a deeper conviction_delta assessment.
    """

    thesis_id: str
    ticker: str

    verdict: InvalidationVerdict = Field(
        description="AI confirmation: CONFIRMED | SUSPECTED | CLEARED."
    )
    breach_type: BreachType = Field(
        description="Primary breach type that triggered the check."
    )
    breach_summary: str = Field(
        description="One-sentence summary of the breach for bot alert."
    )
    narrative: str = Field(
        description=(
            "2-3 sentence explanation of why the thesis is (or is not) invalidated. "
            "Written for the investor, not for internal logging."
        )
    )
    action: Literal["exit_signal", "review", "reduce", "hold"] = Field(
        default="hold",
        description=(
            "exit_signal: thesis is dead, consider exit. "
            "review: escalate to ThesisJudge / manual review. "
            "reduce: partial exit, monitor closely. "
            "hold: breach cleared or context insufficient."
        ),
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="AI confidence in verdict. <0.5 = low, treat as SUSPECTED."
    )
    mitigating_factors: list[str] = Field(
        default_factory=list,
        description="Factors that partially offset the breach signal."
    )
    checked_at: str = Field(
        default="",
        description="ISO 8601 timestamp — stamped by agent after parse."
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("mitigating_factors", mode="before")
    @classmethod
    def ensure_list(cls, v: object) -> list[object]:
        if isinstance(v, str):
            return [v]
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]
