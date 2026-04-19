"""Thesis repository — async DB access for thesis segment.

Owner: thesis segment.
Only ThesisService and ReviewService call this.
readmodel segment uses its own optimized read queries.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.thesis.models import Assumption, Catalyst, Thesis, ThesisReview, ThesisStatus


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
                selectinload(Thesis.reviews),
            )
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

    async def save(self, thesis: Thesis) -> Thesis:
        """Persist (insert or update) a thesis and eager-reload relationships.

        SQLAlchemy expires all attributes after flush(). Any access to
        lazy-loaded relationships (assumptions, catalysts) outside the
        session context will raise MissingGreenlet. We refresh explicitly
        here so that Pydantic serialization in the API layer is safe.
        """
        self._session.add(thesis)
        await self._session.flush()
        await self._session.refresh(
            thesis,
            attribute_names=["assumptions", "catalysts", "reviews"],
        )
        return thesis

    async def delete(self, thesis: Thesis) -> None:
        await self._session.delete(thesis)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Assumption queries
    # ------------------------------------------------------------------

    async def get_assumption_by_id(
        self, assumption_id: int, thesis_id: int
    ) -> Assumption | None:
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

    async def get_catalyst_by_id(
        self, catalyst_id: int, thesis_id: int
    ) -> Catalyst | None:
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
    # Review-specific queries
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
