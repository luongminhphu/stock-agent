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
from src.thesis.timeline_parser import parse_timeline_to_date

logger = get_logger(__name__)

# Re-export tất cả public symbols cũ để backward compat
__all__ = [
    "ThesisService",
    "CreateThesisInput",
    "UpdateThesisInput",
    "AddAssumptionInput",
    "UpdateAssumptionInput",
    "AddCatalystInput",
    "UpdateCatalystInput",
    "ThesisNotFoundError",
    "ThesisAlreadyClosedError",
    "AssumptionNotFoundError",
    "CatalystNotFoundError",
    "parse_timeline_to_date",
]


def _resolve_user_id(user_id: str | None) -> str:
    resolved = user_id or settings.owner_user_id
    if not resolved:
        raise ValueError(
            "user_id is required. Set owner_user_id in settings/.env for single-user mode."
        )
    return resolved


async def _emit_thesis_closed(thesis: Thesis, close_reason: str) -> None:
    """Fire-and-forget: emit ThesisClosedEvent after DB commit.

    Non-blocking — any failure in the event bus does not affect the caller.
    outcome_pnl_pct is intentionally None here; DecisionLog may not be
    populated yet. PostMortemService can query LessonService if needed.
    """
    try:
        from src.platform.event_bus import get_event_bus
        from src.platform.events import ThesisClosedEvent
        event = ThesisClosedEvent(
            thesis_id=thesis.id,
            user_id=thesis.user_id,
            ticker=thesis.ticker,
            close_reason=close_reason,
            thesis_title=thesis.title or "",
            thesis_summary=thesis.summary or "",
            outcome_pnl_pct=None,
        )
        await get_event_bus().publish(event)
    except Exception as exc:
        logger.warning(
            "thesis.close.event_emit_failed",
            thesis_id=thesis.id,
            close_reason=close_reason,
            error=str(exc),
        )


class ThesisService:
    """Thesis lifecycle: create, update, close, invalidate, delete.

    Assumption/Catalyst/Recommendation CRUD được delegate sang ComponentService
    thông qua các proxy methods để giữ interface không thay đổi với callers.

    Caller cung cấp AsyncSession per-request (từ get_db_session()).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = ThesisRepository(session)
        self._components = ComponentService(session)

    # ------------------------------------------------------------------
    # Thesis lifecycle
    # ------------------------------------------------------------------

    async def create(self, inp: CreateThesisInput) -> Thesis:
        from src.thesis.models import Assumption, AssumptionStatus, Catalyst
        thesis = Thesis(
            user_id=_resolve_user_id(inp.user_id),
            ticker=inp.ticker.upper(),
            title=inp.title,
            summary=inp.summary,
            direction=inp.direction,
            status=ThesisStatus.ACTIVE,
            entry_price=inp.entry_price,
            target_price=inp.target_price,
            stop_loss=inp.stop_loss,
        )
        for desc in inp.assumptions or []:
            thesis.assumptions.append(
                Assumption(description=desc, status=AssumptionStatus.PENDING)
            )
        for cat_inp in inp.catalysts or []:
            resolved_date = cat_inp.expected_date or parse_timeline_to_date(cat_inp.timeline)
            thesis.catalysts.append(
                Catalyst(
                    description=cat_inp.description,
                    status=cat_inp.status,
                    expected_date=resolved_date,
                    note=cat_inp.note,
                )
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
        if inp.direction is not None:
            thesis.direction = inp.direction
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
        thesis.closed_at = datetime.now(UTC)
        await self._repo.save(thesis)
        logger.info("thesis.closed", thesis_id=thesis_id)
        await _emit_thesis_closed(thesis, close_reason="closed")
        return thesis

    async def invalidate(self, thesis_id: int, user_id: str) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        thesis.status = ThesisStatus.INVALIDATED
        thesis.closed_at = datetime.now(UTC)
        await self._repo.save(thesis)
        logger.info("thesis.invalidated", thesis_id=thesis_id)
        await _emit_thesis_closed(thesis, close_reason="invalidated")
        return thesis

    async def delete(self, thesis_id: int, user_id: str) -> None:
        thesis = await self._get_owned(thesis_id, user_id)
        await self._repo.delete(thesis)
        logger.info("thesis.deleted", thesis_id=thesis_id)

    async def get(self, thesis_id: int, user_id: str) -> Thesis:
        return await self._get_owned(thesis_id, user_id)

    async def list_for_user(
        self,
        user_id: str | None = None,
        status: ThesisStatus | None = None,
    ) -> list[Thesis]:
        user_id = _resolve_user_id(user_id)
        return await self._repo.list_by_user(user_id, status)

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

    # ------------------------------------------------------------------
    # Assumption proxy
    # ------------------------------------------------------------------

    async def add_assumption(
        self, thesis_id: int, user_id: str, inp: AddAssumptionInput
    ) -> Assumption:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        return await self._components.add_assumption(thesis_id, inp)

    async def update_assumption(
        self, thesis_id: int, assumption_id: int, user_id: str, inp: UpdateAssumptionInput
    ) -> Assumption:
        await self._get_owned(thesis_id, user_id)
        return await self._components.update_assumption(thesis_id, assumption_id, inp)

    async def delete_assumption(
        self, thesis_id: int, assumption_id: int, user_id: str
    ) -> None:
        await self._get_owned(thesis_id, user_id)
        await self._components.delete_assumption(thesis_id, assumption_id)

    # ------------------------------------------------------------------
    # Catalyst proxy
    # ------------------------------------------------------------------

    async def add_catalyst(
        self, thesis_id: int, user_id: str, inp: AddCatalystInput
    ) -> Catalyst:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        return await self._components.add_catalyst(thesis_id, inp)

    async def add_catalyst_from_timeline(
        self,
        thesis_id: int,
        user_id: str | None,
        description: str,
        timeline: str | None,
        note: str | None = None,
    ) -> Catalyst:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)
        return await self._components.add_catalyst_from_timeline(
            thesis_id=thesis_id,
            user_id=user_id,
            description=description,
            timeline=timeline,
            note=note,
        )

    async def update_catalyst(
        self, thesis_id: int, catalyst_id: int, user_id: str, inp: UpdateCatalystInput
    ) -> Catalyst:
        await self._get_owned(thesis_id, user_id)
        return await self._components.update_catalyst(thesis_id, catalyst_id, inp)

    async def delete_catalyst(
        self, thesis_id: int, catalyst_id: int, user_id: str
    ) -> None:
        await self._get_owned(thesis_id, user_id)
        await self._components.delete_catalyst(thesis_id, catalyst_id)

    # ------------------------------------------------------------------
    # Recommendation proxy
    # ------------------------------------------------------------------

    async def apply_recommendation(
        self,
        thesis_id: int,
        recommendation_id: int,
        user_id: str,
        accept: bool,
    ) -> None:
        await self._get_owned(thesis_id, user_id)
        await self._components.apply_recommendation(thesis_id, recommendation_id, accept)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_owned(self, thesis_id: int, user_id: str | None) -> Thesis:
        resolved = _resolve_user_id(user_id)
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None or thesis.user_id != resolved:
            raise ThesisNotFoundError(
                f"Thesis {thesis_id} not found for user {resolved}"
            )
        return thesis

    @staticmethod
    def _assert_mutable(thesis: Thesis) -> None:
        if thesis.status in (ThesisStatus.CLOSED, ThesisStatus.INVALIDATED):
            raise ThesisAlreadyClosedError(
                f"Thesis {thesis.id} is already {thesis.status} and cannot be modified."
            )
