"""Thesis repository — async DB access for thesis segment.

Owner: thesis segment.
Only ThesisService and ReviewService call this.
readmodel segment uses its own optimized read queries.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.thesis.models import (
    Assumption,
    Catalyst,
    CatalystStatus,
    RecommendationStatus,
    ReviewRecommendation,
    Thesis,
    ThesisReview,
    ThesisSnapshot,
    ThesisStatus,
)


class ThesisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Thesis queries
    # ------------------------------------------------------------------

    async def get_by_id(self, thesis_id: int) -> Thesis | None:
        stmt = (
            select(Thesis)
            .where(Thesis.id == thesis_id)
            .options(
                selectinload(Thesis.assumptions),
                selectinload(Thesis.catalysts),
                selectinload(Thesis.reviews).selectinload(ThesisReview.recommendations),
                selectinload(Thesis.snapshots),
            )
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: str,
        status: ThesisStatus | None = None,
    ) -> list[Thesis]:
        stmt = (
            select(Thesis)
            .where(Thesis.user_id == user_id)
            .options(
                selectinload(Thesis.assumptions),
                selectinload(Thesis.catalysts),
                selectinload(Thesis.reviews),
            )
            .order_by(Thesis.created_at.desc())
        )
        if status:
            stmt = stmt.where(Thesis.status == status)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_by_ticker(self, ticker: str) -> list[Thesis]:
        stmt = (
            select(Thesis)
            .where(Thesis.ticker == ticker.upper())
            .where(Thesis.status == ThesisStatus.ACTIVE)
            .options(selectinload(Thesis.assumptions), selectinload(Thesis.catalysts))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_for_user(self, user_id: str) -> list[Thesis]:
        """Return all ACTIVE theses for a user, with assumptions + catalysts loaded.

        Used by auto_expire_overdue_catalysts — needs catalysts eager-loaded.
        """
        stmt = (
            select(Thesis)
            .where(Thesis.user_id == user_id)
            .where(Thesis.status == ThesisStatus.ACTIVE)
            .options(
                selectinload(Thesis.assumptions),
                selectinload(Thesis.catalysts),
            )
            .order_by(Thesis.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_stale_theses(self, user_id: str, stale_days: int = 3) -> list[Thesis]:
        """Return ACTIVE theses that have not been reviewed in the last stale_days days.

        A thesis is considered stale if:
          - It has never been reviewed, OR
          - Its most recent review is older than stale_days days.

        Uses a correlated subquery to find the max reviewed_at per thesis.
        Loads assumptions + catalysts so ReviewService can build context without
        extra queries.
        """
        cutoff = datetime.now(UTC) - timedelta(days=stale_days)

        # Subquery: latest reviewed_at per thesis
        latest_review_sq = (
            select(ThesisReview.reviewed_at)
            .where(ThesisReview.thesis_id == Thesis.id)
            .order_by(ThesisReview.reviewed_at.desc())
            .limit(1)
            .correlate(Thesis)
            .scalar_subquery()
        )

        stmt = (
            select(Thesis)
            .where(Thesis.user_id == user_id)
            .where(Thesis.status == ThesisStatus.ACTIVE)
            .where(
                # No review at all, OR latest review is older than cutoff
                (latest_review_sq == None) | (latest_review_sq < cutoff)  # noqa: E711
            )
            .options(
                selectinload(Thesis.assumptions),
                selectinload(Thesis.catalysts),
            )
            .order_by(Thesis.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def save(self, thesis: Thesis) -> Thesis:
        """Persist (insert or update) a thesis.

        flush() → expire all attrs → re-fetch với selectinload để
        Pydantic serialization ngoài session không bị MissingGreenlet.
        """
        self._session.add(thesis)
        await self._session.flush()
        stmt = (
            select(Thesis)
            .where(Thesis.id == thesis.id)
            .options(
                selectinload(Thesis.assumptions),
                selectinload(Thesis.catalysts),
                selectinload(Thesis.reviews),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def delete(self, thesis: Thesis) -> None:
        await self._session.delete(thesis)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Assumption queries
    # ------------------------------------------------------------------

    async def get_assumption_by_id(self, assumption_id: int, thesis_id: int) -> Assumption | None:
        """Fetch an assumption, scoped to a specific thesis for safety."""
        stmt = (
            select(Assumption)
            .where(Assumption.id == assumption_id)
            .where(Assumption.thesis_id == thesis_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def save_assumption(self, assumption: Assumption) -> Assumption:
        self._session.add(assumption)
        await self._session.flush()
        await self._session.refresh(assumption)
        return assumption

    async def delete_assumption(self, assumption: Assumption) -> None:
        await self._session.delete(assumption)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Catalyst queries
    # ------------------------------------------------------------------

    async def get_catalyst_by_id(self, catalyst_id: int, thesis_id: int) -> Catalyst | None:
        """Fetch a catalyst, scoped to a specific thesis for safety."""
        stmt = (
            select(Catalyst)
            .where(Catalyst.id == catalyst_id)
            .where(Catalyst.thesis_id == thesis_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def save_catalyst(self, catalyst: Catalyst) -> Catalyst:
        self._session.add(catalyst)
        await self._session.flush()
        await self._session.refresh(catalyst)
        return catalyst

    async def delete_catalyst(self, catalyst: Catalyst) -> None:
        await self._session.delete(catalyst)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Review queries
    # ------------------------------------------------------------------

    async def save_review(self, review: ThesisReview) -> ThesisReview:
        """Persist a ThesisReview record."""
        self._session.add(review)
        await self._session.flush()
        await self._session.refresh(review)
        return review

    async def list_reviews_by_thesis(
        self,
        thesis_id: int,
        limit: int = 10,
    ) -> list[ThesisReview]:
        """Return recent reviews for a thesis, newest first."""
        stmt = (
            select(ThesisReview)
            .where(ThesisReview.thesis_id == thesis_id)
            .order_by(ThesisReview.reviewed_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_review(self, thesis_id: int) -> ThesisReview | None:
        """Return the most recent review for a thesis."""
        stmt = (
            select(ThesisReview)
            .where(ThesisReview.thesis_id == thesis_id)
            .order_by(ThesisReview.reviewed_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_reviews_batch(
        self,
        thesis_ids: list[int],
        limit_per_thesis: int = 5,
    ) -> dict[int, list[ThesisReview]]:
        """Fetch recent reviews for multiple theses in a single IN-query.

        Replaces N×get_latest_review + N×list_reviews_by_thesis sequential calls
        used by BriefingService._build_thesis_judge_block (B3 fix).

        Returns a dict keyed by thesis_id. Each value is a list of
        ThesisReview ordered newest-first, capped at limit_per_thesis rows
        per thesis (sliced in Python after the single DB round-trip).

        Empty list is returned for any thesis_id that has no reviews.
        Returns {} immediately when thesis_ids is empty.

        Owner: thesis segment. Called by briefing segment (adapter use only).
        """
        if not thesis_ids:
            return {}

        stmt = (
            select(ThesisReview)
            .where(ThesisReview.thesis_id.in_(thesis_ids))
            .order_by(ThesisReview.thesis_id, ThesisReview.reviewed_at.desc())
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        # Group by thesis_id, preserving newest-first order
        grouped: dict[int, list[ThesisReview]] = defaultdict(list)
        for row in rows:
            grouped[row.thesis_id].append(row)

        return {
            tid: reviews[:limit_per_thesis]
            for tid, reviews in grouped.items()
        }

    # ------------------------------------------------------------------
    # Snapshot queries
    # ------------------------------------------------------------------

    async def save_snapshot(self, snapshot: ThesisSnapshot) -> ThesisSnapshot:
        """Persist a ThesisSnapshot record."""
        self._session.add(snapshot)
        await self._session.flush()
        await self._session.refresh(snapshot)
        return snapshot

    async def list_snapshots(
        self,
        thesis_id: int,
        limit: int = 30,
    ) -> list[ThesisSnapshot]:
        """Return recent snapshots for a thesis, newest first."""
        stmt = (
            select(ThesisSnapshot)
            .where(ThesisSnapshot.thesis_id == thesis_id)
            .order_by(ThesisSnapshot.snapshotted_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # ReviewRecommendation helpers
    # ------------------------------------------------------------------

    async def update_recommendation_status(
        self,
        recommendation_id: int,
        status: RecommendationStatus,
    ) -> ReviewRecommendation | None:
        """Update a ReviewRecommendation status in-place. Returns the updated object."""
        stmt = select(ReviewRecommendation).where(ReviewRecommendation.id == recommendation_id)
        result = await self._session.execute(stmt)
        rec = result.scalar_one_or_none()
        if rec is None:
            return None
        rec.status = status
        await self._session.flush()
        return rec

    async def list_pending_recommendations(
        self,
        thesis_id: int,
    ) -> list[ReviewRecommendation]:
        """Return all PENDING recommendations for a thesis."""
        stmt = (
            select(ReviewRecommendation)
            .join(ThesisReview, ReviewRecommendation.review_id == ThesisReview.id)
            .where(ThesisReview.thesis_id == thesis_id)
            .where(ReviewRecommendation.status == RecommendationStatus.PENDING)
            .order_by(ReviewRecommendation.id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def save_recommendations(
        self,
        recs: list[ReviewRecommendation],
    ) -> None:
        """Persist a batch of ReviewRecommendation records.

        Called by ReviewService after AI review to store auto-applied
        recommendations. No commit here – caller owns transaction boundary.
        """
        if not recs:
            return
        self._session.add_all(recs)
        await self._session.flush()

    async def get_catalyst_status_summary(self, thesis_id: int) -> dict[CatalystStatus, int]:
        """Return count of catalysts grouped by status for a thesis.

        Used by ThesisService.get_thesis_health to report catalyst breakdown
        without loading full Catalyst objects.

        Returns a dict mapping CatalystStatus → count. Missing statuses have
        count 0 (defaultdict behaviour).
        """
        from sqlalchemy import func

        stmt = (
            select(Catalyst.status, func.count(Catalyst.id).label("cnt"))
            .where(Catalyst.thesis_id == thesis_id)
            .group_by(Catalyst.status)
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        summary: dict[CatalystStatus, int] = defaultdict(int)
        for row in rows:
            summary[row.status] = row.cnt
        return dict(summary)
