"""DashboardService — optimised read queries for the user dashboard.

Owner: readmodel segment.

Design rules:
- SELECT only columns needed; never load full ORM graphs.
- No writes. No business logic. No AI calls.
- Current price is fetched from market segment and injected into DTOs;
  the SQL query itself never touches market tables.
- All public methods are async and accept an AsyncSession.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.readmodel.schemas import (
    DashboardResponse,
    ThesisSummaryRow,
    WatchlistSnapshotRow,
)


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_dashboard(self, user_id: str) -> DashboardResponse:
        """Build full dashboard payload for a user."""
        rows = await self._thesis_summary_rows(user_id)
        active = sum(1 for r in rows if r.status == "active")
        invalidated = sum(1 for r in rows if r.status == "invalidated")
        closed = sum(1 for r in rows if r.status == "closed")
        scores = [r.score for r in rows if r.score is not None]
        avg_score = sum(scores) / len(scores) if scores else None
        return DashboardResponse(
            user_id=user_id,
            generated_at=datetime.now(timezone.utc),
            total_theses=len(rows),
            active_count=active,
            invalidated_count=invalidated,
            closed_count=closed,
            avg_score=avg_score,
            theses=rows,
        )

    async def get_watchlist_snapshot(
        self, user_id: str
    ) -> list[WatchlistSnapshotRow]:
        """Watchlist items joined with linked thesis summary."""
        from src.thesis.models import Thesis  # read-only join
        from src.watchlist.models import WatchlistItem

        stmt = (
            select(
                WatchlistItem.ticker,
                WatchlistItem.note,
                WatchlistItem.thesis_id,
                WatchlistItem.added_at,
                Thesis.title.label("thesis_title"),
                Thesis.status.label("thesis_status"),
            )
            .outerjoin(Thesis, Thesis.id == WatchlistItem.thesis_id)
            .where(WatchlistItem.user_id == user_id)
            .order_by(WatchlistItem.added_at.desc())
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        return [
            WatchlistSnapshotRow(
                ticker=r.ticker,
                note=r.note,
                thesis_id=r.thesis_id,
                thesis_title=r.thesis_title,
                thesis_status=str(r.thesis_status) if r.thesis_status else None,
                current_price=None,  # caller injects from market segment
                added_at=r.added_at,
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _thesis_summary_rows(
        self, user_id: str
    ) -> list[ThesisSummaryRow]:
        from src.thesis.models import (
            Assumption,
            AssumptionStatus,
            Catalyst,
            CatalystStatus,
            Thesis,
            ThesisReview,
        )

        # Latest review per thesis via correlated subquery.
        latest_review_subq = (
            select(
                ThesisReview.thesis_id,
                ThesisReview.verdict,
                ThesisReview.reviewed_at,
            )
            .distinct(ThesisReview.thesis_id)
            .order_by(
                ThesisReview.thesis_id,
                ThesisReview.reviewed_at.desc(),
            )
            .subquery("latest_review")
        )

        total_assumptions_subq = (
            select(
                Assumption.thesis_id,
                func.count(Assumption.id).label("total"),
                func.sum(
                    func.cast(
                        Assumption.status == AssumptionStatus.INVALID, Integer
                    )
                ).label("invalid"),
            )
            .group_by(Assumption.thesis_id)
            .subquery("assumption_counts")
        )

        total_catalysts_subq = (
            select(
                Catalyst.thesis_id,
                func.count(Catalyst.id).label("total"),
                func.sum(
                    func.cast(
                        Catalyst.status == CatalystStatus.TRIGGERED, Integer
                    )
                ).label("triggered"),
            )
            .group_by(Catalyst.thesis_id)
            .subquery("catalyst_counts")
        )

        stmt = (
            select(
                Thesis.id,
                Thesis.ticker,
                Thesis.title,
                Thesis.status,
                Thesis.score,
                Thesis.entry_price,
                Thesis.target_price,
                Thesis.stop_loss,
                Thesis.created_at,
                latest_review_subq.c.verdict.label("last_verdict"),
                latest_review_subq.c.reviewed_at.label("last_reviewed_at"),
                func.coalesce(total_assumptions_subq.c.total, 0).label("assumption_count"),
                func.coalesce(total_assumptions_subq.c.invalid, 0).label("invalid_assumption_count"),
                func.coalesce(total_catalysts_subq.c.total, 0).label("catalyst_count"),
                func.coalesce(total_catalysts_subq.c.triggered, 0).label("triggered_catalyst_count"),
            )
            .outerjoin(latest_review_subq, latest_review_subq.c.thesis_id == Thesis.id)
            .outerjoin(total_assumptions_subq, total_assumptions_subq.c.thesis_id == Thesis.id)
            .outerjoin(total_catalysts_subq, total_catalysts_subq.c.thesis_id == Thesis.id)
            .where(Thesis.user_id == user_id)
            .order_by(Thesis.created_at.desc())
        )

        result = await self._session.execute(stmt)
        rows = result.all()

        out: list[ThesisSummaryRow] = []
        for r in rows:
            upside_pct: float | None = None
            risk_reward: float | None = None
            if r.entry_price and r.target_price and r.entry_price > 0:
                upside_pct = (r.target_price - r.entry_price) / r.entry_price * 100
            if (
                r.entry_price
                and r.target_price
                and r.stop_loss
                and r.entry_price > r.stop_loss
            ):
                upside = r.target_price - r.entry_price
                downside = r.entry_price - r.stop_loss
                if downside > 0:
                    risk_reward = upside / downside

            out.append(
                ThesisSummaryRow(
                    id=r.id,
                    ticker=r.ticker,
                    title=r.title,
                    status=str(r.status.value if hasattr(r.status, "value") else r.status),
                    score=r.score,
                    entry_price=r.entry_price,
                    target_price=r.target_price,
                    stop_loss=r.stop_loss,
                    upside_pct=upside_pct,
                    risk_reward=risk_reward,
                    current_price=None,
                    pnl_pct=None,
                    last_verdict=str(r.last_verdict) if r.last_verdict else None,
                    last_reviewed_at=r.last_reviewed_at,
                    created_at=r.created_at,
                    assumption_count=r.assumption_count or 0,
                    invalid_assumption_count=r.invalid_assumption_count or 0,
                    catalyst_count=r.catalyst_count or 0,
                    triggered_catalyst_count=r.triggered_catalyst_count or 0,
                )
            )
        return out
