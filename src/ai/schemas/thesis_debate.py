"""Thesis Debate Mode output schema.

Owner: ai segment.
Distinct from ThesisJudgeOutput:
  - JudgeOutput: verdict + conviction_delta for auto-triggered signal cross-check.
  - DebateOutput: structured adversarial challenges for user-initiated deep review.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class DebateStance(StrEnum):
    BULL = "bull"        # AI finds thesis fundamentally sound
    BEAR = "bear"        # AI finds thesis fundamentally challenged
    NEUTRAL = "neutral"  # Mixed — valid points on both sides


class ChallengeStrength(StrEnum):
    WEAK = "weak"        # Minor concern, can proceed
    MODERATE = "moderate"  # Worth addressing before acting
    STRONG = "strong"    # Must resolve before acting
    FATAL = "fatal"      # Thesis may be wrong at the core


class DebateChallenge(BaseModel):
    """A single adversarial challenge targeting the thesis."""

    id: int
    claim: str = Field(
        description="Luận điểm phản biện ngắn gọn (1-2 câu)"
    )
    evidence: str = Field(
        description="Bằng chứng / dữ liệu / logic cụ thể hỗ trợ challenge này"
    )
    targets_assumption: str | None = Field(
        None,
        description="ID hoặc mô tả assumption bị tấn công trực tiếp. None nếu attack toàn bộ thesis.",
    )
    strength: ChallengeStrength
    rebuttal_hint: str = Field(
        description="Gợi ý cụ thể để investor có thể phản biện lại challenge này"
    )


class DebateOutput(BaseModel):
    """Structured output from ThesisDebateAgent.

    Downstream consumers:
      - API route: returns directly as JSON response.
      - Bot: formats challenges into Discord embed.
      - Future: persist as thesis_debate_log for learning loop.
    """

    ticker: str
    thesis_id: str
    stance: DebateStance

    # Core adversarial output
    bull_case_summary: str = Field(
        description="Tóm tắt lý do thesis CÓ THỂ đúng (2-3 câu). Trung thực — không inflate."
    )
    bear_case_summary: str = Field(
        description="Tóm tắt lý do thesis CÓ THỂ sai (2-3 câu). Cụ thể — không chung chung."
    )
    challenges: list[DebateChallenge] = Field(
        description="2-5 challenges cụ thể, có evidence. Sorted by strength DESC."
    )

    # Verdict fields
    weakest_link: str = Field(
        description="Assumption yếu nhất trong thesis hiện tại — điểm dễ bị invalidate nhất"
    )
    key_question: str = Field(
        description="Câu hỏi quan trọng nhất investor cần tự trả lời trước khi hành động"
    )
    confidence_adjustment: float = Field(
        ge=-1.0,
        le=1.0,
        description=(
            "Gợi ý điều chỉnh conviction dựa trên debate. "
            "-1.0=exit hoàn toàn, 0.0=giữ nguyên, +1.0=double down. "
            "Thường trong range [-0.4, +0.3] trừ trường hợp extreme."
        ),
    )
    debate_verdict: str = Field(
        description="1-2 câu kết luận thẳng thắn từ góc nhìn devil's advocate"
    )

    # Stamped by agent at runtime
    debated_at: str = ""
