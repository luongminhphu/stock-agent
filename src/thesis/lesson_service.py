"""LessonService — query persisted AI lessons from DecisionLog.

Owner: thesis segment.

Responsibilities:
- Fetch recent key_lesson + pattern_detected for a user.
- Format them as plain text snippets ready for prompt injection.

Non-responsibilities:
- Does not call AI.
- Does not write DecisionLog (that belongs to DecisionService.persist_lesson).
- Does not own briefing or pretrade prompt assembly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thesis.models import DecisionLog

_DEFAULT_LOOKBACK_DAYS = 90
_DEFAULT_MAX_LESSONS = 5


@dataclass(frozen=True)
class LessonSnippet:
    """One persisted AI lesson, ready for prompt injection."""
    decision_id: int
    ticker: str
    decision_type: str
    outcome_verdict: str | None
    key_lesson: str
    pattern_detected: str | None
    decision_at: str  # ISO 8601 string


class LessonService:
    """Read-only view into persisted lessons from the Decision Replay loop.

    Two usage patterns:

    1. Low-level (get + format separately):
        snippets = await svc.get_recent_lessons(user_id)
        text = svc.format_for_prompt(snippets)

    2. High-level convenience (used by BriefingService + PreTradeService):
        text = await svc.build_lesson_context(user_id, ticker=ticker, limit=3)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    async def get_recent_lessons(
        self,
        user_id: str,
        *,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
        max_lessons: int = _DEFAULT_MAX_LESSONS,
        ticker: str | None = None,
    ) -> list[LessonSnippet]:
        """Return the most recent key_lesson entries for a user.

        Args:
            user_id:       Filter to this investor.
            lookback_days: Only consider decisions within this window.
            max_lessons:   Cap the number of returned snippets.
            ticker:        Optionally filter to a single ticker.

        Returns:
            List of LessonSnippet sorted newest-first, capped at max_lessons.
        """
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

        conditions = [
            DecisionLog.user_id == user_id,
            DecisionLog.key_lesson.isnot(None),
            DecisionLog.decision_at >= cutoff,
        ]
        if ticker:
            conditions.append(DecisionLog.ticker == ticker.upper())

        stmt = (
            select(DecisionLog)
            .where(and_(*conditions))
            .order_by(DecisionLog.decision_at.desc())
            .limit(max_lessons)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            LessonSnippet(
                decision_id=r.id,
                ticker=r.ticker,
                decision_type=r.decision_type,
                outcome_verdict=r.outcome_verdict,
                key_lesson=r.key_lesson,
                pattern_detected=r.pattern_detected,
                decision_at=r.decision_at.isoformat(),
            )
            for r in rows
        ]

    async def build_lesson_context(
        self,
        user_id: str,
        *,
        ticker: str | None = None,
        limit: int = _DEFAULT_MAX_LESSONS,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    ) -> str:
        """Convenience wrapper: fetch + format in one call.

        Used by BriefingService and PreTradeService to inject past lessons
        into AI prompt context without needing to handle LessonSnippet objects.

        Args:
            user_id:       Investor to query.
            ticker:        Optional ticker filter (PreTrade uses this,
                           Briefing leaves it None for cross-ticker lessons).
            limit:         Max snippets to include (PreTrade: 3, Briefing: 5).
            lookback_days: How far back to look.

        Returns:
            Formatted string ready for prompt injection,
            or empty string if no lessons found.
        """
        snippets = await self.get_recent_lessons(
            user_id,
            lookback_days=lookback_days,
            max_lessons=limit,
            ticker=ticker,
        )
        return self.format_for_prompt(snippets)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_for_prompt(snippets: list[LessonSnippet]) -> str:
        """Render snippets as a compact multi-line string for prompt injection.

        Output example (injected into briefing / pretrade context)::

            === Past lessons from your decision history ===
            [2026-02-10] BUY VCB → CORRECT | Lesson: Breakout signal confirmed by
            volume surge was reliable when market breadth was positive.
            | Pattern: breakout_chasing
            [2026-01-03] BUY HPG → INCORRECT | Lesson: Entered before catalyst
            materialized; waited too short after earnings miss.
            | Pattern: premature_entry

        Returns empty string if snippets is empty (caller skips injection).
        """
        if not snippets:
            return ""

        lines = ["=== Past lessons from your decision history ==="]
        for s in snippets:
            verdict_part = f" → {s.outcome_verdict}" if s.outcome_verdict else ""
            date_str = s.decision_at[:10]  # YYYY-MM-DD
            pattern_part = f" | Pattern: {s.pattern_detected}" if s.pattern_detected else ""
            lines.append(
                f"[{date_str}] {s.decision_type} {s.ticker}{verdict_part} "
                f"| Lesson: {s.key_lesson}{pattern_part}"
            )
        return "\n".join(lines)
