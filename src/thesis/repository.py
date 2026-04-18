"""Thesis repository — async DB access for thesis segment.

Owner: thesis segment.
Only ThesisService and ReviewService call this.
readmodel segment uses its own optimized read queries.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.thesis.models import Thesis, ThesisStatus


class ThesisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        self._session.add(thesis)
        await self._session.flush()  # get generated id without full commit
        return thesis

    async def delete(self, thesis: Thesis) -> None:
        await self._session.delete(thesis)
        await self._session.flush()
