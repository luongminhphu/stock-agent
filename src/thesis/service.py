"""Thesis service — lifecycle operations for the thesis segment.

Owner: thesis segment.
Entry point duy nhất cho bot commands và API routes.

ThesisService chỉ chịu trách nhiệm thesis lifecycle:
  create / update / close / invalidate / delete / get / list

Assumption, Catalyst, Recommendation CRUD → component_service.py
Input DTOs + Exceptions                  → dtos.py
Timeline string parser                   → timeline_parser.py
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.config import settings
from src.platform.logging import get_logger
from src.thesis.component_service import ComponentService
from src.thesis.dtos import (
    AddAssumptionInput,
    AddCatalystInput,
    AssumptionNotFoundError,
    CatalystNotFoundError,
    CreateThesisInput,
    ThesisAlreadyClosedError,
    ThesisNotFoundError,
    UpdateAssumptionInput,
    UpdateCatalystInput,
    UpdateThesisInput,
)
from src.thesis.models import (
    Assumption,
    Catalyst,
    Thesis,
    ThesisStatus,
)
from src.thesis.repository import ThesisRepository
from src.thesis.timeline_parser import parse_time_horizon

logger = get_logger(__name__)

_DEFAULT_USER_ID = "default_user"


def _resolve_user_id(user_id: str | None) -> str:
    return user_id if user_id is not None else _DEFAULT_USER_ID


class ThesisService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ThesisRepository(session)
        self._components = ComponentService(session)

    # ------------------------------------------------------------------
    # Thesis CRUD
    # ------------------------------------------------------------------

    async def create(self, user_id: str, inp: CreateThesisInput) -> Thesis:
        ticker = inp.ticker.upper().strip()
        time_horizon = parse_time_horizon(inp.time_horizon) if inp.time_horizon else None
        thesis = Thesis(
            user_id=user_id,
            ticker=ticker,
            entry_thesis=inp.entry_thesis,
            target_price=inp.target_price,
            stop_loss=inp.stop_loss,
            time_horizon=time_horizon,
            direction=getattr(inp, "direction", None),
        )
        self._session.add(thesis)
        await self._session.flush()
        logger.info(
            "thesis.created",
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            user_id=user_id,
        )
        return thesis

    async def update(
        self, thesis_id: int, user_id: str, inp: UpdateThesisInput
    ) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        _assert_mutable(thesis)
        data = inp.model_dump(exclude_unset=True)
        if "time_horizon" in data and data["time_horizon"] is not None:
            data["time_horizon"] = parse_time_horizon(data["time_horizon"])
        for field, value in data.items():
            setattr(thesis, field, value)
        logger.info("thesis.updated", thesis_id=thesis_id, fields=list(data))
        return thesis

    async def close(
        self,
        thesis_id: int,
        user_id: str,
        outcome: str = "completed",
    ) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        if thesis.status == ThesisStatus.CLOSED:
            raise ThesisAlreadyClosedError(thesis_id)
        thesis.status = ThesisStatus.CLOSED
        thesis.closed_at = datetime.now(UTC)
        thesis.close_outcome = outcome
        logger.info("thesis.closed", thesis_id=thesis_id, outcome=outcome)
        return thesis

    async def delete(self, thesis_id: int, user_id: str) -> None:
        thesis = await self._get_owned(thesis_id, user_id)
        await self._session.delete(thesis)
        logger.info("thesis.deleted", thesis_id=thesis_id)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get(self, thesis_id: int, user_id: str) -> Thesis:
        return await self._get_owned(thesis_id, user_id)

    async def list_for_user(
        self,
        user_id: str | None = None,
        status: ThesisStatus | None = None,
    ) -> list[Thesis]:
        user_id = _resolve_user_id(user_id)
        return await self._repo.list_by_user(user_id, status)

    async def list_active(self, user_id: str) -> list[Thesis]:
        """Return all ACTIVE theses for a user.

        Alias for list_for_user(user_id, status=ThesisStatus.ACTIVE).
        Called by briefing context_builder for thesis health context.
        """
        return await self.list_for_user(user_id=user_id, status=ThesisStatus.ACTIVE)

    async def get_active_thesis_id_for_ticker(
        self,
        ticker: str,
        user_id: str | None = None,
    ) -> str | None:
        """Return str(thesis.id) of the first ACTIVE thesis for ticker."""
        resolved = _resolve_user_id(user_id)
        theses = await self._repo.list_active_by_ticker(ticker)
        user_theses = [t for t in theses if t.user_id == resolved]
        if not user_theses:
            return None
        return str(user_theses[0].id)

    async def get_thesis_context_for_ticker(
        self,
        ticker: str,
        user_id: str | None = None,
    ) -> str:
        """Return a compact text context block for the active thesis on ticker."""
        resolved = _resolve_user_id(user_id)
        theses = await self._repo.list_active_by_ticker(ticker)
        user_theses = [t for t in theses if t.user_id == resolved]
        if not user_theses:
            return ""
        thesis = user_theses[0]
        parts = [f"Thesis [{thesis.id}] {thesis.ticker}: {thesis.entry_thesis}"]
        if thesis.target_price:
            parts.append(f"Target: {thesis.target_price}")
        if thesis.stop_loss:
            parts.append(f"Stop: {thesis.stop_loss}")
        if thesis.time_horizon:
            parts.append(f"Horizon: {thesis.time_horizon}")
        return " | ".join(parts)

    async def get_thesis_health(
        self,
        user_id: str | None = None,
    ) -> list[dict]:
        """Return health snapshot of all active theses for the user."""
        resolved = _resolve_user_id(user_id)
        try:
            theses = await self.list_active(user_id=resolved)
        except Exception as exc:
            logger.warning(
                "thesis_health.list_active_failed",
                user_id=resolved,
                error=str(exc),
            )
            return []

        results = []
        for thesis in theses:
            try:
                assumptions = await self._components.list_assumptions(thesis.id)
                reviews = await self._components.list_reviews(thesis.id)
            except Exception as exc:
                logger.warning(
                    "thesis_health.detail_failed",
                    thesis_id=thesis.id,
                    error=str(exc),
                )
                assumptions, reviews = [], []

            last_review_at = None
            if reviews:
                last_review_at = max(
                    (getattr(r, "created_at", None) for r in reviews),
                    default=None,
                )

            days_since_review = None
            if last_review_at is not None:
                now = datetime.now(UTC)
                if last_review_at.tzinfo is None:
                    last_review_at = last_review_at.replace(tzinfo=UTC)
                days_since_review = (now - last_review_at).days

            results.append(
                {
                    "id": thesis.id,
                    "ticker": thesis.ticker,
                    "status": thesis.status,
                    "entry_thesis": thesis.entry_thesis,
                    "target_price": thesis.target_price,
                    "stop_loss": thesis.stop_loss,
                    "time_horizon": thesis.time_horizon,
                    "assumption_count": len(assumptions),
                    "last_review_at": last_review_at,
                    "days_since_review": days_since_review,
                }
            )
        return results

    # ------------------------------------------------------------------
    # Assumption proxy
    # ------------------------------------------------------------------

    async def add_assumption(
        self, thesis_id: int, user_id: str, inp: AddAssumptionInput
    ) -> Assumption:
        thesis = await self._get_owned(thesis_id, user_id)
        _assert_mutable(thesis)
        return await self._components.add_assumption(thesis_id, inp)

    async def update_assumption(
        self,
        thesis_id: int,
        assumption_id: int,
        user_id: str,
        inp: UpdateAssumptionInput,
    ) -> Assumption:
        thesis = await self._get_owned(thesis_id, user_id)
        _assert_mutable(thesis)
        return await self._components.update_assumption(assumption_id, inp)

    async def delete_assumption(
        self, thesis_id: int, assumption_id: int, user_id: str
    ) -> None:
        thesis = await self._get_owned(thesis_id, user_id)
        _assert_mutable(thesis)
        await self._components.delete_assumption(assumption_id)

    async def list_assumptions(
        self, thesis_id: int, user_id: str
    ) -> list[Assumption]:
        await self._get_owned(thesis_id, user_id)
        return await self._components.list_assumptions(thesis_id)

    # ------------------------------------------------------------------
    # Catalyst proxy
    # ------------------------------------------------------------------

    async def add_catalyst(
        self, thesis_id: int, user_id: str, inp: AddCatalystInput
    ) -> Catalyst:
        thesis = await self._get_owned(thesis_id, user_id)
        _assert_mutable(thesis)
        return await self._components.add_catalyst(thesis_id, inp)

    async def update_catalyst(
        self,
        thesis_id: int,
        catalyst_id: int,
        user_id: str,
        inp: UpdateCatalystInput,
    ) -> Catalyst:
        thesis = await self._get_owned(thesis_id, user_id)
        _assert_mutable(thesis)
        return await self._components.update_catalyst(catalyst_id, inp)

    async def delete_catalyst(
        self, thesis_id: int, catalyst_id: int, user_id: str
    ) -> None:
        thesis = await self._get_owned(thesis_id, user_id)
        _assert_mutable(thesis)
        await self._components.delete_catalyst(catalyst_id)

    async def list_catalysts(
        self, thesis_id: int, user_id: str
    ) -> list[Catalyst]:
        await self._get_owned(thesis_id, user_id)
        return await self._components.list_catalysts(thesis_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_owned(self, thesis_id: int, user_id: str) -> Thesis:
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None:
            raise ThesisNotFoundError(thesis_id)
        if thesis.user_id != user_id:
            if not settings.is_development:
                raise PermissionError(
                    f"User {user_id!r} does not own thesis {thesis_id}"
                )
        return thesis


def _assert_mutable(thesis: Thesis) -> None:
    if thesis.status == ThesisStatus.CLOSED:
        raise ThesisAlreadyClosedError(thesis.id)
