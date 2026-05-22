"""Thesis Debate Mode output schema.

Owner: ai segment.
Contract is derived from bot.commands.debate_embeds — field names and enum
values here must stay in sync with that consumer.

Distinct from ThesisJudgeOutput:
  - JudgeOutput: verdict + conviction_delta for auto-triggered signal cross-check.
  - DebateOutput: structured adversarial challenges for user-initiated deep review.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ChallengeStrength(StrEnum):
    CRITICAL = "critical"       # Must resolve before acting — thesis may be wrong at core
    SIGNIFICANT = "significant" # Meaningfully weakens the thesis
    MODERATE = "moderate"       # Worth addressing; manageable risk
    MINOR = "minor"             # Low concern, can proceed


class OverallStance(StrEnum):
    BULL = "bull"       # AI finds thesis fundamentally sound
    BEAR = "bear"       # AI finds thesis fundamentally challenged
    NEUTRAL = "neutral" # Mixed — valid points on both sides


class DebateChallenge(BaseModel):
    """A single adversarial challenge targeting the thesis."""

    area: str = Field(
        description="Lĩnh vực bị challenge (ví dụ: valuation, catalyst, timing, macro)"
    )
    challenge: str = Field(
        description="Luận điểm phản biện cụ thể (1-3 câu, có evidence/logic rõ ràng)"
    )
    strength: ChallengeStrength
    counter_argument: str | None = Field(
        None,
        description="Gợi ý phản biện lại challenge này. None nếu không có hướng rõ ràng.",
    )


class DebateOutput(BaseModel):
    """Structured output from ThesisDebateAgent.

    Downstream consumers:
      - bot.debate_embeds: formats challenges into Discord embed.
      - API route: returns directly as JSON response.
      - Future: persist as thesis_debate_log for learning loop.
    """

    verdict: str = Field(
        description="1-2 câu kết luận thẳng thắn từ góc nhìn devil's advocate"
    )
    overall_stance: OverallStance
    confidence: float = Field(
        ge=0.0,
        le=100.0,
        description="Mức độ tin cậy AI vào stance này (0-100)",
    )
    challenges: list[DebateChallenge] = Field(
        description="2-8 challenges cụ thể, có evidence. Sorted by strength DESC."
    )
    suggested_action: str | None = Field(
        None,
        description="Hành động gợi ý cho investor sau debate. None nếu chưa rõ ràng.",
    )

    # Stamped by agent at runtime
    debated_at: str = ""
