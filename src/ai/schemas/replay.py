"""
Schemas for ReplayAgent — post-mortem of a past decision.

Owner: ai segment.

Classes:
  OutcomeVerdict      — WIN / LOSS / BREAK_EVEN / PENDING
  PatternTag          — canonical behavior pattern labels for ReplayAgent output
  ReplayOutput        — structured output from ReplayAgent (AI-generated)
  ReplayOutcomeRecord — typed record persisted after ReplayAgent runs;
                         consumed by LessonService and thesis scoring downstream
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from src.ai.schemas._base import _coerce_confidence


class OutcomeVerdict(StrEnum):
    WIN        = "WIN"
    LOSS       = "LOSS"
    BREAK_EVEN = "BREAK_EVEN"
    PENDING    = "PENDING"


class PatternTag(StrEnum):
    """Canonical behavioral pattern labels that ReplayAgent may detect.

    ReplayAgent MUST use one of these values in pattern_tag (or null).
    Downstream services (LessonService, thesis scoring) use this field
    to aggregate patterns across decisions and surface recurring behaviors.

    Labels:
      fomo_entry       — entered after a breakout move, chasing price
      early_exit       — exited before thesis target was reached
      ignored_stop_loss— held through the thesis stop_loss level
      thesis_drift     — the original thesis changed but position was kept
      correct_conviction— held with discipline, thesis played out correctly
      sized_correctly  — position size was appropriate for the risk taken
      oversized        — position was too large relative to risk/conviction
    """

    FOMO_ENTRY         = "fomo_entry"
    EARLY_EXIT         = "early_exit"
    IGNORED_STOP_LOSS  = "ignored_stop_loss"
    THESIS_DRIFT       = "thesis_drift"
    CORRECT_CONVICTION = "correct_conviction"
    SIZED_CORRECTLY    = "sized_correctly"
    OVERSIZED          = "oversized"


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
    pattern_tag: PatternTag | None = Field(
        default=None,
        description=(
            "Canonical behavioral pattern detected in this decision. "
            "Must be a PatternTag value or null if no clear pattern. "
            "Used by LessonService to aggregate recurring behaviors."
        ),
    )
    exit_reason_assessment: str = Field(
        default="",
        description=(
            "Assessment of whether the stated exit_reason was correct — "
            "required when exit_reason is provided in context. "
            "E.g.: 'Stop_loss was triggered correctly — thesis had already "
            "been invalidated by declining revenue.'"
        ),
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

    @field_validator("pattern_tag", mode="before")
    @classmethod
    def coerce_pattern_tag(cls, v: object) -> PatternTag | None:
        if v is None or v == "":
            return None
        if isinstance(v, PatternTag):
            return v
        try:
            return PatternTag(str(v).lower())
        except ValueError:
            return None


@dataclass
class ReplayOutcomeRecord:
    """Typed record of a completed ReplayAgent run.

    Owner: ai segment (schema only).
    Created by: ReplayAgent / ReplayService after agent call.
    Consumed by:
      - LessonService: persists lessons[] and pattern_tag for future
        brief personalization and warning injection.
      - Thesis scoring: uses thesis_accuracy_note + outcome_verdict to
        update thesis score accuracy over time (future Wave).

    Contract:
      - Immutable after construction.
      - Never contains SQLAlchemy ORM objects.
      - trade_id links back to the Trade row that triggered the replay.
      - pattern_tag is None when ReplayAgent found no clear behavioral pattern.

    Loop position:
      Trade.SELL (portfolio) → ReplayAgent (ai) → ReplayOutcomeRecord (ai)
      → LessonService (ai) → brief personalization (briefing)
      → prioritized_actions reason enrichment (briefing)
    """

    user_id: str
    trade_id: int                         # FK to Trade.id that triggered replay
    ticker: str
    outcome_verdict: OutcomeVerdict
    outcome_pnl_pct: float | None
    pattern_tag: PatternTag | None
    lessons: list[str] = field(default_factory=list)
    thesis_accuracy_note: str = ""
    exit_reason_assessment: str = ""
    summary: str = ""
    replayed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_replay_output(
        cls,
        output: ReplayOutput,
        user_id: str,
        trade_id: int,
    ) -> "ReplayOutcomeRecord":
        """Construct from ReplayOutput returned by ReplayAgent.

        Usage::
            result = await replay_agent.run(ctx)
            record = ReplayOutcomeRecord.from_replay_output(
                output=result, user_id=user_id, trade_id=trade.id
            )
            await lesson_service.persist_replay(record)
        """
        return cls(
            user_id=user_id,
            trade_id=trade_id,
            ticker=output.ticker,
            outcome_verdict=output.outcome_verdict,
            outcome_pnl_pct=output.outcome_pnl_pct,
            pattern_tag=output.pattern_tag,
            lessons=output.lessons,
            thesis_accuracy_note=output.thesis_accuracy_note,
            exit_reason_assessment=output.exit_reason_assessment,
            summary=output.summary,
        )
