"""LessonService — extract past decision lessons for AI context injection.

Owner: thesis segment.

Responsibilities:
- Query DecisionLog records that have been evaluated (outcome_evaluated_at is not None).
- Format lessons as a concise string for injection into AI agent prompts.
- Optionally filter by ticker for ticker-specific context (pretrade use case).

Non-responsibilities:
- Does not call AI.
- Does not mutate any record.
- Does not own briefing or pretrade logic.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.models import DecisionLog

logger = get_logger(__name__)

_DEFAULT_LESSON_LIMIT = 5


class LessonService:
    """Read-only service — provides formatted past lessons for prompt injection."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build_lesson_context(
        self,
        user_id: str,
        *,
        ticker: str | None = None,
        limit: int = _DEFAULT_LESSON_LIMIT,
    ) -> str:
        """Return a formatted string of recent evaluated decisions.

        Args:
            user_id:  Filter to this investor only.
            ticker:   When provided, return lessons for this ticker only
                      (pretrade use case). When None, return across all tickers
                      (briefing use case).
            limit:    Max number of lessons to include (default 5).

        Returns:
            Multi-line string ready for prompt injection, or empty string
            when no evaluated decisions exist yet.
        """
        try:
            rows = await self._query(user_id=user_id, ticker=ticker, limit=limit)
        except Exception as exc:
            logger.warning(
                "lesson_service.query_failed",
                user_id=user_id,
                ticker=ticker,
                error=str(exc),
            )
            return ""

        if not rows:
            return ""

        lines = [f"Lịch sử {len(rows)} quyết định đã được đánh giá của nhà đầu tư này:"]
        for row in rows:
            verdict_icon = {"CORRECT": "✅", "INCORRECT": "❌", "MIXED": "🟡"}.get(
                str(row.outcome_verdict).upper(), "⚪"
            )
            pnl_str = f"{row.outcome_pnl_pct:+.1f}%" if row.outcome_pnl_pct is not None else "N/A"
            decision_date = row.decision_at.strftime("%d/%m/%Y") if row.decision_at else "?"

            line = (
                f"{verdict_icon} [{row.ticker}, {row.decision_type}, {row.outcome_verdict} {pnl_str}"
                f", {decision_date}]"
            )
            if row.key_lesson:
                line += f" Lesson: {row.key_lesson}"
            if row.pattern_detected:
                line += f" | Pattern: {row.pattern_detected}"
            lines.append(f"- {line}")

        lines.append(
            "Dùng những bài học trên để cá nhân hóa phân tích: tránh lặp lại pattern thua lỗ,"
            " nhận diện tín hiệu đã từng thành công."
        )
        return "\n".join(lines)

    async def _query(
        self,
        user_id: str,
        ticker: str | None,
        limit: int,
    ) -> list[DecisionLog]:
        stmt = (
            select(DecisionLog)
            .where(
                DecisionLog.user_id == user_id,
                DecisionLog.outcome_evaluated_at.is_not(None),
                DecisionLog.outcome_verdict.is_not(None),
            )
            .order_by(DecisionLog.outcome_evaluated_at.desc())
            .limit(limit)
        )
        if ticker is not None:
            stmt = stmt.where(DecisionLog.ticker == ticker.upper())
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)
