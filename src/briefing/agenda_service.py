"""AgendaService — orchestrate data loading and call AgendaBuilderAgent.

Owner: briefing segment.

Responsibilities:
- Load pending decisions, active theses, memory signals from other segments.
- Build AgendaContext and delegate to AgendaBuilderAgent.
- Does NOT contain business rules for DECIDE/WATCH/DEFER classification.
  That logic lives exclusively in AgendaBuilderAgent (ai segment).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.ai.agents.agenda_builder import AgendaBuilderAgent
from src.ai.prompts.agenda import (
    ActiveThesisItem,
    AgendaContext,
    DailyAgendaResult,
    MemorySignalItem,
    PendingDecisionItem,
)
from src.platform.logging import get_logger
from src.thesis.models import DecisionLog, Thesis

logger = get_logger(__name__)

# Only surface decisions whose horizon deadline is within this window
_HORIZON_WINDOW_DAYS = 10
# Thesis with no review beyond this threshold becomes a WATCH candidate
_STALE_THESIS_THRESHOLD_DAYS = 14


class AgendaService:
    """Builds a DailyAgendaResult for a given user.

    Inject memory_service=None when the memory layer is not yet available;
    memory signals will degrade gracefully to an empty list.
    """

    def __init__(
        self,
        session: AsyncSession,
        agenda_agent: AgendaBuilderAgent,
        memory_service=None,
    ) -> None:
        self._session = session
        self._agent = agenda_agent
        self._memory_svc = memory_service

    async def build_agenda(self, user_id: str) -> DailyAgendaResult | None:
        """Main entry point. Loads context, calls agent, returns result."""
        today = date.today()

        # Run loaders sequentially — all share the same AsyncSession.
        # asyncpg does not allow concurrent queries on a single connection.
        pending_decisions = await self._load_pending_decisions(user_id, today)
        active_theses = await self._load_active_theses(user_id, today)
        memory_signals = await self._load_memory_signals(user_id)
        unreviewed_count = await self._count_unreviewed_lessons(user_id)

        ctx = AgendaContext(
            today=today.isoformat(),
            user_id=user_id,
            pending_decisions=pending_decisions,
            active_theses=active_theses,
            memory_signals=memory_signals,
            unreviewed_lessons_count=unreviewed_count,
        )

        return await self._agent.build(ctx)

    # ------------------------------------------------------------------
    # Private loaders — each loader reads from exactly one segment
    # ------------------------------------------------------------------

    async def _load_pending_decisions(
        self, user_id: str, today: date
    ) -> list[PendingDecisionItem]:
        """Return decisions that are unevaluated and within the horizon window."""
        cutoff = datetime.now(UTC) - timedelta(days=90 + _HORIZON_WINDOW_DAYS)
        stmt = (
            select(DecisionLog)
            .where(
                DecisionLog.user_id == user_id,
                DecisionLog.outcome_evaluated_at.is_(None),
                DecisionLog.decision_at >= cutoff,
            )
            .order_by(DecisionLog.decision_at.asc())
            .limit(20)
        )
        rows = (await self._session.execute(stmt)).scalars().all()

        items = []
        for row in rows:
            deadline = (
                row.decision_at.date() + timedelta(days=row.review_horizon_days)
            )
            days_until = (deadline - today).days
            if days_until <= _HORIZON_WINDOW_DAYS:
                items.append(
                    PendingDecisionItem(
                        decision_id=row.id,
                        ticker=row.ticker,
                        decision_type=row.decision_type,
                        decision_at=row.decision_at.date().isoformat(),
                        horizon_days=row.review_horizon_days,
                        deadline=deadline.isoformat(),
                        days_until_deadline=days_until,
                        pnl_pct=row.outcome_pnl_pct,
                        rationale_summary=(row.rationale or "")[:120] or None,
                    )
                )
        return items

    async def _load_active_theses(
        self, user_id: str, today: date
    ) -> list[ActiveThesisItem]:
        """Load active theses with metadata needed for AI prioritisation."""
        stmt = (
            select(Thesis)
            .where(Thesis.user_id == user_id, Thesis.status == "active")
            .options(selectinload(Thesis.reviews))
            .order_by(Thesis.created_at.desc())
            .limit(15)
        )
        rows = (await self._session.execute(stmt)).scalars().all()

        items = []
        for t in rows:
            created = t.created_at.date() if t.created_at else today
            days_active = (today - created).days

            last_reviewed_days_ago = None
            if getattr(t, "reviews", None):
                valid_reviews = [
                    r for r in t.reviews
                    if getattr(r, "created_at", None) is not None
                ]
                if valid_reviews:
                    latest_review = max(valid_reviews, key=lambda r: r.created_at)
                    last_reviewed_days_ago = (today - latest_review.created_at.date()).days

            next_check = self._find_next_assumption_check(t, today)

            has_pending = any(
                d.outcome_evaluated_at is None
                for d in (getattr(t, "decision_logs", None) or [])
            )

            items.append(
                ActiveThesisItem(
                    thesis_id=t.id,
                    ticker=t.ticker,
                    thesis_title=getattr(t, "title", None) or t.ticker,
                    health_score=self._latest_health_score(t),
                    days_active=days_active,
                    next_assumption_check=next_check,
                    has_pending_decision=has_pending,
                    last_reviewed_days_ago=last_reviewed_days_ago,
                )
            )
        return items

    async def _load_memory_signals(self, user_id: str) -> list[MemorySignalItem]:
        """Load high-confidence memory patterns (>= 0.65) via MemoryService.

        Uses MemoryService.get_memory_context(session, user_id) and extracts
        patterns from latest_snapshot.patterns_json.
        Safe no-op if memory_service is None or on any error.
        """
        if self._memory_svc is None:
            return []
        try:
            import json as _json

            memory_ctx = await self._memory_svc.get_memory_context(
                self._session, user_id
            )
            if memory_ctx is None or memory_ctx.latest_snapshot is None:
                return []
            patterns = _json.loads(memory_ctx.latest_snapshot.patterns_json or "[]")
            return [
                MemorySignalItem(
                    ticker=p.get("ticker", ""),
                    pattern_summary=p.get("description", ""),
                    confidence=float(p.get("confidence", 0)),
                )
                for p in patterns
                if p.get("confidence", 0) >= 0.65 and p.get("ticker")
            ][:5]
        except Exception as exc:  # noqa: BLE001
            logger.warning("agenda_service.memory_load_failed", error=str(exc))
            return []

    async def _count_unreviewed_lessons(self, user_id: str) -> int:
        """Count decisions with key_lesson set (proxy for unreviewed lessons).

        TODO(Wave 2): add lesson_viewed_at column to DecisionLog and filter on it.
        """
        try:
            stmt = select(DecisionLog).where(
                DecisionLog.user_id == user_id,
                DecisionLog.key_lesson.is_not(None),
            )
            rows = (await self._session.execute(stmt)).scalars().all()
            return len(rows)
        except Exception:  # noqa: BLE001
            return 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_next_assumption_check(self, thesis: Thesis, today: date) -> str | None:
        """Return the nearest upcoming assumption check date, if any."""
        components = getattr(thesis, "components", None) or []
        upcoming = [
            c.next_check_date
            for c in components
            if getattr(c, "next_check_date", None) and c.next_check_date >= today
        ]
        return min(upcoming).isoformat() if upcoming else None

    def _latest_health_score(self, thesis: Thesis) -> int | None:
        snapshots = getattr(thesis, "snapshots", None) or []
        if not snapshots:
            return None
        latest = max(snapshots, key=lambda s: s.created_at)
        score = getattr(latest, "score", None)
        return int(score) if score is not None else None
