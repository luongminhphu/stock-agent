"""Thesis service — lifecycle operations for the thesis segment.

Owner: thesis segment.
This is the primary entry point for all thesis write operations.
Bot commands and API routes call this; they do not import models directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.thesis.models import (
    Assumption,
    AssumptionStatus,
    Catalyst,
    CatalystStatus,
    Thesis,
    ThesisStatus,
)
from src.thesis.repository import ThesisRepository
from src.platform.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Input DTOs (plain dataclasses — no ORM, safe to cross boundaries)
# ---------------------------------------------------------------------------


@dataclass
class CreateThesisInput:
    user_id: str
    ticker: str
    title: str
    summary: str = ""
    entry_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    assumptions: list[str] | None = None  # list of description strings
    catalysts: list[str] | None = None  # list of description strings


@dataclass
class UpdateThesisInput:
    title: str | None = None
    summary: str | None = None
    entry_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ThesisNotFoundError(Exception):
    """Raised when a thesis ID does not exist or doesn't belong to the user."""


class ThesisAlreadyClosedError(Exception):
    """Raised when an operation is attempted on a closed/invalidated thesis."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ThesisService:
    """Thesis lifecycle: create, update, close, invalidate.

    Caller provides an AsyncSession per-request (from get_db_session()).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = ThesisRepository(session)

    async def create(self, inp: CreateThesisInput) -> Thesis:
        thesis = Thesis(
            user_id=inp.user_id,
            ticker=inp.ticker.upper(),
            title=inp.title,
            summary=inp.summary,
            status=ThesisStatus.ACTIVE,
            entry_price=inp.entry_price,
            target_price=inp.target_price,
            stop_loss=inp.stop_loss,
        )

        for desc in inp.assumptions or []:
            thesis.assumptions.append(Assumption(description=desc, status=AssumptionStatus.PENDING))
        for desc in inp.catalysts or []:
            thesis.catalysts.append(Catalyst(description=desc, status=CatalystStatus.PENDING))

        await self._repo.save(thesis)
        logger.info("thesis.created", thesis_id=thesis.id, ticker=thesis.ticker)
        return thesis

    async def update(self, thesis_id: int, user_id: str, inp: UpdateThesisInput) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)

        if inp.title is not None:
            thesis.title = inp.title
        if inp.summary is not None:
            thesis.summary = inp.summary
        if inp.entry_price is not None:
            thesis.entry_price = inp.entry_price
        if inp.target_price is not None:
            thesis.target_price = inp.target_price
        if inp.stop_loss is not None:
            thesis.stop_loss = inp.stop_loss

        await self._repo.save(thesis)
        logger.info("thesis.updated", thesis_id=thesis_id)
        return thesis

    async def close(self, thesis_id: int, user_id: str) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        thesis.status = ThesisStatus.CLOSED
        thesis.closed_at = datetime.utcnow()
        await self._repo.save(thesis)
        logger.info("thesis.closed", thesis_id=thesis_id)
        return thesis

    async def invalidate(self, thesis_id: int, user_id: str) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        thesis.status = ThesisStatus.INVALIDATED
        thesis.closed_at = datetime.utcnow()
        await self._repo.save(thesis)
        logger.info("thesis.invalidated", thesis_id=thesis_id)
        return thesis

    async def get(self, thesis_id: int, user_id: str) -> Thesis:
        return await self._get_owned(thesis_id, user_id)

    async def list_for_user(
        self,
        user_id: str,
        status: ThesisStatus | None = None,
    ) -> list[Thesis]:
        return await self._repo.list_by_user(user_id, status)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_owned(self, thesis_id: int, user_id: str) -> Thesis:
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None or thesis.user_id != user_id:
            raise ThesisNotFoundError(f"Thesis {thesis_id} not found for user {user_id}")
        return thesis

    @staticmethod
    def _assert_mutable(thesis: Thesis) -> None:
        if thesis.status in (ThesisStatus.CLOSED, ThesisStatus.INVALIDATED):
            raise ThesisAlreadyClosedError(
                f"Thesis {thesis.id} is already {thesis.status} and cannot be modified."
            )
