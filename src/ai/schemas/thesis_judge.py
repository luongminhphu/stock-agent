"""
Schemas for ThesisJudgeAgent.

Owner: ai segment.
Trigger: SignalEngineOutput.thesis_review_triggers.
Caller: BriefingService — runs batch after SignalEngine, before BriefingAgent LLM call.

Note on conviction_delta:
  Changed from ThesisConvictionDelta (StrEnum) to float [-1.0, 1.0].
  Rationale: the fallback in thesis_judge.py already produces float values
  (-0.6, -0.35, -0.2, 0.0). Using float is simpler and consistent with
  downstream analytics (conviction timeline, readmodel). ThesisConvictionDelta
  enum is kept below as a reference/label mapping only.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import _coerce_confidence


class ThesisConvictionDelta(StrEnum):
    """Reference labels for conviction_delta magnitude — not used in schema.

    Kept for documentation and any future label-mapping utilities.
    Approximate mapping:
      STRONG_INCREASE  ->  delta >= +0.5
      INCREASE         ->  +0.2 <= delta < +0.5
      UNCHANGED        ->  -0.1 < delta < +0.2
      DECREASE         ->  -0.5 < delta <= -0.1
      STRONG_DECREASE  ->  delta <= -0.5
    """

    STRONG_INCREASE = "STRONG_INCREASE"
    INCREASE = "INCREASE"
    UNCHANGED = "UNCHANGED"
    DECREASE = "DECREASE"
    STRONG_DECREASE = "STRONG_DECREASE"


class ThesisJudgeVerdict(StrEnum):
    STRENGTHENING = "STRENGTHENING"
    ON_TRACK = "ON_TRACK"
    WEAKENING = "WEAKENING"
    INVALIDATED = "INVALIDATED"


class ChallengedAssumption(BaseModel):
    """An assumption being challenged in a thesis judge evaluation."""

    assumption_text: str
    challenge_reason: str
    severity: Literal["minor", "major", "critical"]


class ThesisJudgeOutput(BaseModel):
    """Structured output from ThesisJudgeAgent.

    conviction_delta is a float in [-1.0, 1.0].
    Negative = conviction decreasing, positive = increasing.
    Use ThesisConvictionDelta enum labels as reference for magnitude buckets.
    """

    ticker: str
    thesis_id: str
    verdict: ThesisJudgeVerdict
    conviction_delta: float = Field(
        ge=-1.0,
        le=1.0,
        description=(
            "Change in conviction: negative = weakening, positive = strengthening. "
            "Range [-1.0, 1.0]. See ThesisConvictionDelta for magnitude labels."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0)
    challenged_assumptions: list[ChallengedAssumption] = Field(default_factory=list)
    new_risks: list[str] = Field(default_factory=list)
    action: Literal["hold", "reduce", "review", "exit_signal"] = Field(default="hold")
    reasoning: str
    judged_at: str = Field(default="", description="ISO 8601 timestamp — set by agent after parse.")

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("conviction_delta", mode="before")
    @classmethod
    def coerce_delta(cls, v: object) -> float:
        """Coerce conviction_delta to float, clamped to [-1.0, 1.0]."""
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
        return max(-1.0, min(1.0, f))

    @field_validator("challenged_assumptions", mode="before")
    @classmethod
    def ensure_challenged_list(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]

    @field_validator("new_risks", mode="before")
    @classmethod
    def ensure_risks_list(cls, v: object) -> list[object]:
        if isinstance(v, str):
            return [v]
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]
