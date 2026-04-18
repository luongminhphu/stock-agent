"""LeaderboardService — ranked view of theses by score or PnL.

Owner: readmodel segment.
Read-only. No writes, no AI calls, no business logic.
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.readmodel.schemas import LeaderboardEntry, LeaderboardResponse


class LeaderboardService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_leaderboard(
        self,
        user_id: str,
        sort_by: Literal["score", "pnl"] = "score",
        limit: int = 20,
    ) -> LeaderboardResponse:
        from src.thesis.models import Thesis, ThesisReview, ThesisSnapshot

        # Latest review verdict per thesis
        latest_review_subq = (
            select(
                ThesisReview.thesis_id,
                ThesisReview.verdict,
            )
            .distinct(ThesisReview.thesis_id)
            .order_by(
                ThesisReview.thesis_id,
                ThesisReview.reviewed_at.desc(),
            )
            .subquery("latest_review")
        )

        # Latest pnl_pct from snapshots
        latest_snapshot_subq = (
            select(
                ThesisSnapshot.thesis_id,
                ThesisSnapshot.pnl_pct,
            )
            .distinct(ThesisSnapshot.thesis_id)
            .order_by(
                ThesisSnapshot.thesis_id,
                ThesisSnapshot.snapshotted_at.desc(),
            )
            .subquery("latest_snapshot")
        )

        sort_col = (
            Thesis.score.desc().nulls_last()
            if sort_by == "score"
            else latest_snapshot_subq.c.pnl_pct.desc().nulls_last()
        )

        stmt = (
            select(
                Thesis.id,
                Thesis.ticker,
                Thesis.title,
                Thesis.score,
                Thesis.status,
                Thesis.created_at,
                latest_review_subq.c.verdict.label("last_verdict"),
                latest_snapshot_subq.c.pnl_pct,
            )
            .outerjoin(latest_review_subq, latest_review_subq.c.thesis_id == Thesis.id)
            .outerjoin(latest_snapshot_subq, latest_snapshot_subq.c.thesis_id == Thesis.id)
            .where(Thesis.user_id == user_id)
            .order_by(sort_col)
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        rows = result.all()

        entries = [
            LeaderboardEntry(
                rank=idx + 1,
                thesis_id=r.id,
                ticker=r.ticker,
                title=r.title,
                score=r.score,
                pnl_pct=r.pnl_pct,
                last_verdict=str(r.last_verdict) if r.last_verdict else None,
                status=str(r.status.value if hasattr(r.status, "value") else r.status),
                created_at=r.created_at,
            )
            for idx, r in enumerate(rows)
        ]

        return LeaderboardResponse(
            user_id=user_id,
            sort_by=sort_by,
            entries=entries,
        )
