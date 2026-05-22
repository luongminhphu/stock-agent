"""ThesisDebateAgent — devil's advocate phản biện thesis có cấu trúc.

Owner: ai segment.

Distinct từ ThesisJudgeAgent:
  - ThesisJudgeAgent:  auto-triggered by signals, fast cross-check, feeds briefing.
  - ThesisDebateAgent: user-initiated, deep adversarial analysis, returns structured
                       challenges for the investor to reason through.

Responsibility:
  - Nhận thesis metadata + optional price context + optional debate_focus.
  - Trả về DebateOutput: bull/bear summary, challenges với evidence, verdict.
  - Sort challenges by strength DESC để downstream render CRITICAL trước.
  - KHÔNG write DB — caller owns persistence decision.
  - KHÔNG emit events — fire-and-return only.
  - KHÔNG tự gọi DB hay market API — tất cả context được inject bởi caller.

Caller:
  - API route: POST /readmodel/dashboard/theses/{id}/debate (Wave C.2)
  - Bot: !debate command (Wave C.2)

Fallback:
  - AI unavailable → _fallback_debate() với empty challenges + neutral stance.
  - confidence=0.0 signals degraded quality to downstream.
  - Never raises — caller always gets a DebateOutput.

Memory logging (Wave C.3, optional):
  - Accept session + user_id for future episodic memory integration.
  - Pattern mirrors ThesisJudgeAgent._log_thesis_judge_interaction.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from src.ai.client import AIClient, AIError
from src.ai.prompts.thesis_debate import SPEC, build_user_prompt
from src.ai.schemas.thesis_debate import (
    ChallengeStrength,
    DebateOutput,
    OverallStance,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Strength ordering for sort (CRITICAL first, MINOR last)
_STRENGTH_ORDER: dict[ChallengeStrength, int] = {
    ChallengeStrength.CRITICAL: 0,
    ChallengeStrength.SIGNIFICANT: 1,
    ChallengeStrength.MODERATE: 2,
    ChallengeStrength.MINOR: 3,
}


def _fallback_debate(
    thesis_id: str | int,
    ticker: str,
    thesis_title: str,
) -> DebateOutput:
    """Rule-based fallback when AI is unavailable.

    Returns a neutral DebateOutput with empty challenges.
    confidence_adjustment=0.0 signals no information gain.
    Caller should surface a user-facing message about degraded quality.
    """
    return DebateOutput(
        ticker=ticker,
        thesis_id=str(thesis_id),
        stance=OverallStance.NEUTRAL,
        bull_case_summary="AI không available — không thể phân tích bull case.",
        bear_case_summary="AI không available — không thể phân tích bear case.",
        challenges=[],
        weakest_link="Không xác định được — cần AI để phân tích.",
        key_question="AI tạm thời không available. Vui lòng thử lại sau.",
        confidence_adjustment=0.0,
        debate_verdict="Debate mode tạm thời không available. Kết quả không có giá trị phân tích.",
        debated_at=datetime.now(UTC).isoformat(),
    )


class ThesisDebateAgent:
    """Generates structured adversarial challenges for an investment thesis.

    Example usage (from API route)::

        agent = ThesisDebateAgent(ai_client)
        result = await agent.run(
            thesis_id=42,
            ticker="VHM",
            thesis_title="VHM phục hồi sau áp lực margin call",
            thesis_summary="Luận điểm: VHM đang ở vùng đáy chu kỳ...",
            assumptions=[
                {"id": 1, "description": "Lãi suất tiếp tục giảm H2", "status": "active"},
            ],
            catalysts=[
                {"id": 2, "description": "KQKD Q2 2026 > kỳ vọng", "status": "pending"},
            ],
            invalidation_conditions=["Margin call lần 2", "P/B vượt 2.0x"],
            price_context={"price": 42500, "change_1w": "-2.1%", "change_1m": "-8.3%"},
            debate_focus="entry",  # optional: entry | exit | sizing | None
        )
        # result.challenges sorted CRITICAL → SIGNIFICANT → MODERATE → MINOR
    """

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def run(
        self,
        *,
        thesis_id: str | int,
        ticker: str,
        thesis_title: str,
        thesis_summary: str,
        assumptions: list[dict[str, Any]],
        catalysts: list[dict[str, Any]],
        invalidation_conditions: list[str],
        price_context: dict[str, Any] | None = None,
        recent_news: list[str] | None = None,
        days_since_written: int | None = None,
        conviction_current: float | None = None,
        debate_focus: str | None = None,
        session: Any = None,
        user_id: str | None = None,
    ) -> DebateOutput:
        """Run Thesis Debate Mode for a single thesis.

        Args:
            thesis_id:               Thesis ID for traceability.
            ticker:                  Mã cổ phiếu.
            thesis_title:            Tiêu đề thesis.
            thesis_summary:          Luận điểm đầy đủ.
            assumptions:             Active assumptions [{"id", "description", "status"}].
            catalysts:               Pending catalysts [{"id", "description", "status"}].
            invalidation_conditions: Explicit invalidation conditions.
            price_context:           Optional live price data from QuoteService.
                                     Keys: price, change_1w, change_1m, volume_trend.
            recent_news:             Optional recent news headlines (max 5).
            days_since_written:      Days since thesis was created.
            conviction_current:      Current conviction (0.0-1.0).
            debate_focus:            Narrow the debate: "entry" | "exit" | "sizing" | None.
            session:                 Optional DB session (reserved for Wave C.3 memory logging).
            user_id:                 Optional user ID (reserved for Wave C.3 memory logging).

        Returns:
            DebateOutput — always. Never raises.
            On AI failure: fallback with empty challenges, stance=NEUTRAL.
        """
        user_prompt = build_user_prompt(
            thesis_id=thesis_id,
            ticker=ticker,
            thesis_title=thesis_title,
            thesis_summary=thesis_summary,
            assumptions=assumptions,
            catalysts=catalysts,
            invalidation_conditions=invalidation_conditions,
            price_context=price_context,
            recent_news=recent_news,
            days_since_written=days_since_written,
            conviction_current=conviction_current,
            debate_focus=debate_focus,
        )

        try:
            result: DebateOutput = await self._client.structured_call(
                spec=SPEC,
                user_prompt=user_prompt,
            )

            # Stamp runtime fields
            result.thesis_id = str(thesis_id)
            result.ticker = ticker
            result.debated_at = datetime.now(UTC).isoformat()

            # Sort challenges CRITICAL → SIGNIFICANT → MODERATE → MINOR
            result.challenges.sort(
                key=lambda c: _STRENGTH_ORDER.get(c.strength, 99)
            )

            logger.info(
                "ThesisDebate: thesis=%s ticker=%s stance=%s "
                "challenges=%d adj=%+.2f weakest_link=%r",
                thesis_id,
                ticker,
                result.stance,
                len(result.challenges),
                result.confidence_adjustment,
                result.weakest_link[:60] if result.weakest_link else "",
            )
            return result

        except AIError as exc:
            exc_type = type(exc).__name__
            is_rate_limit = "rate" in exc_type.lower() or "ratelimit" in exc_type.lower()
            if is_rate_limit:
                logger.info(
                    "ThesisDebate: rate limit thesis=%s ticker=%s — returning fallback",
                    thesis_id,
                    ticker,
                )
            else:
                logger.warning(
                    "ThesisDebate: AI error thesis=%s ticker=%s: %s",
                    thesis_id,
                    ticker,
                    exc,
                )
            return _fallback_debate(thesis_id, ticker, thesis_title)

        except (json.JSONDecodeError, ValidationError) as exc:
            # Parse/schema error may signal prompt regression — log at ERROR
            logger.error(
                "ThesisDebate: parse error thesis=%s ticker=%s "
                "— possible prompt regression: %s",
                thesis_id,
                ticker,
                exc,
            )
            return _fallback_debate(thesis_id, ticker, thesis_title)

        except Exception as exc:
            logger.warning(
                "ThesisDebate: unexpected error thesis=%s ticker=%s: %s",
                thesis_id,
                ticker,
                exc,
            )
            return _fallback_debate(thesis_id, ticker, thesis_title)
