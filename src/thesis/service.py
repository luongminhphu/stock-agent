"""Thesis service — lifecycle operations for the thesis segment.

Owner: thesis segment.
This is the primary entry point for all thesis write operations.
Bot commands and API routes call this; they do not import models directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

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
    catalysts: list[str] | None = None   # list of description strings


@dataclass
class UpdateThesisInput:
    title: str | None = None
    summary: str | None = None
    entry_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None


@dataclass
class AddAssumptionInput:
    description: str
    status: AssumptionStatus = AssumptionStatus.PENDING
    note: str | None = None


@dataclass
class UpdateAssumptionInput:
    description: str | None = None
    status: AssumptionStatus | None = None
    note: str | None = None


@dataclass
class AddCatalystInput:
    description: str
    status: CatalystStatus = CatalystStatus.PENDING
    expected_date: datetime | None = None
    note: str | None = None


@dataclass
class UpdateCatalystInput:
    description: str | None = None
    status: CatalystStatus | None = None
    expected_date: datetime | None = None
    triggered_at: datetime | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ThesisNotFoundError(Exception):
    """Raised when a thesis ID does not exist or doesn't belong to the user."""


class ThesisAlreadyClosedError(Exception):
    """Raised when an operation is attempted on a closed/invalidated thesis."""


class AssumptionNotFoundError(Exception):
    """Raised when an assumption does not exist within a thesis."""


class CatalystNotFoundError(Exception):
    """Raised when a catalyst does not exist within a thesis."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ThesisService:
    """Thesis lifecycle: create, update, close, invalidate, delete.
    Also owns assumption and catalyst CRUD within a thesis.

    Caller provides an AsyncSession per-request (from get_db_session()).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = ThesisRepository(session)

    # ------------------------------------------------------------------
    # Thesis CRUD
    # ------------------------------------------------------------------

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
            thesis.assumptions.append(
                Assumption(description=desc, status=AssumptionStatus.PENDING)
            )
        for desc in inp.catalysts or []:
            thesis.catalysts.append(
                Catalyst(description=desc, status=CatalystStatus.PENDING)
            )

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
        thesis.closed_at = datetime.now(timezone.utc)
        await self._repo.save(thesis)
        logger.info("thesis.closed", thesis_id=thesis_id)
        return thesis

    async def invalidate(self, thesis_id: int, user_id: str) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        thesis.status = ThesisStatus.INVALIDATED
        thesis.closed_at = datetime.now(timezone.utc)
        await self._repo.save(thesis)
        logger.info("thesis.invalidated", thesis_id=thesis_id)
        return thesis

    async def delete(self, thesis_id: int, user_id: str) -> None:
        """Hard delete a thesis and all its children (cascade)."""
        thesis = await self._get_owned(thesis_id, user_id)
        await self._repo.delete(thesis)
        logger.info("thesis.deleted", thesis_id=thesis_id)

    async def get(self, thesis_id: int, user_id: str) -> Thesis:
        return await self._get_owned(thesis_id, user_id)

    async def list_for_user(
        self,
        user_id: str,
        status: ThesisStatus | None = None,
    ) -> list[Thesis]:
        return await self._repo.list_by_user(user_id, status)

    # ------------------------------------------------------------------
    # Assumption CRUD
    # ------------------------------------------------------------------

    async def add_assumption(
        self, thesis_id: int, user_id: str, inp: AddAssumptionInput
    ) -> Assumption:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)

        assumption = Assumption(
            thesis_id=thesis_id,
            description=inp.description,
            status=inp.status,
            note=inp.note,
        )
        await self._repo.save_assumption(assumption)
        logger.info("assumption.added", thesis_id=thesis_id, assumption_id=assumption.id)
        return assumption

    async def update_assumption(
        self,
        thesis_id: int,
        assumption_id: int,
        user_id: str,
        inp: UpdateAssumptionInput,
    ) -> Assumption:
        await self._get_owned(thesis_id, user_id)  # ownership check
        assumption = await self._repo.get_assumption_by_id(assumption_id, thesis_id)
        if assumption is None:
            raise AssumptionNotFoundError(
                f"Assumption {assumption_id} not found in thesis {thesis_id}"
            )

        if inp.description is not None:
            assumption.description = inp.description
        if inp.status is not None:
            assumption.status = inp.status
        if inp.note is not None:
            assumption.note = inp.note

        await self._repo.save_assumption(assumption)
        logger.info("assumption.updated", assumption_id=assumption_id)
        return assumption

    async def delete_assumption(
        self, thesis_id: int, assumption_id: int, user_id: str
    ) -> None:
        await self._get_owned(thesis_id, user_id)  # ownership check
        assumption = await self._repo.get_assumption_by_id(assumption_id, thesis_id)
        if assumption is None:
            raise AssumptionNotFoundError(
                f"Assumption {assumption_id} not found in thesis {thesis_id}"
            )
        await self._repo.delete_assumption(assumption)
        logger.info("assumption.deleted", assumption_id=assumption_id, thesis_id=thesis_id)

    # ------------------------------------------------------------------
    # Catalyst CRUD
    # ------------------------------------------------------------------

    async def add_catalyst(
        self, thesis_id: int, user_id: str, inp: AddCatalystInput
    ) -> Catalyst:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)

        catalyst = Catalyst(
            thesis_id=thesis_id,
            description=inp.description,
            status=inp.status,
            expected_date=inp.expected_date,
            note=inp.note,
        )
        await self._repo.save_catalyst(catalyst)
        logger.info("catalyst.added", thesis_id=thesis_id, catalyst_id=catalyst.id)
        return catalyst

    async def update_catalyst(
        self,
        thesis_id: int,
        catalyst_id: int,
        user_id: str,
        inp: UpdateCatalystInput,
    ) -> Catalyst:
        await self._get_owned(thesis_id, user_id)  # ownership check
        catalyst = await self._repo.get_catalyst_by_id(catalyst_id, thesis_id)
        if catalyst is None:
            raise CatalystNotFoundError(
                f"Catalyst {catalyst_id} not found in thesis {thesis_id}"
            )

        if inp.description is not None:
            catalyst.description = inp.description
        if inp.status is not None:
            catalyst.status = inp.status
        if inp.expected_date is not None:
            catalyst.expected_date = inp.expected_date
        if inp.triggered_at is not None:
            catalyst.triggered_at = inp.triggered_at
        if inp.note is not None:
            catalyst.note = inp.note

        await self._repo.save_catalyst(catalyst)
        logger.info("catalyst.updated", catalyst_id=catalyst_id)
        return catalyst

    async def delete_catalyst(
        self, thesis_id: int, catalyst_id: int, user_id: str
    ) -> None:
        await self._get_owned(thesis_id, user_id)  # ownership check
        catalyst = await self._repo.get_catalyst_by_id(catalyst_id, thesis_id)
        if catalyst is None:
            raise CatalystNotFoundError(
                f"Catalyst {catalyst_id} not found in thesis {thesis_id}"
            )
        await self._repo.delete_catalyst(catalyst)
        logger.info("catalyst.deleted", catalyst_id=catalyst_id, thesis_id=thesis_id)

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
