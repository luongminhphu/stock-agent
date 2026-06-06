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
]


def _resolve_user_id(user_id: str | None) -> str:
    if user_id is not None:
        return user_id
    default = getattr(settings, "DEFAULT_USER_ID", None)
    if default:
        return default
    raise ValueError("user_id is required")


async def _emit_thesis_closed(thesis: Thesis, close_reason: str) -> None:
    """Fire-and-forget event emission. Failure is silent."""
    try:
        from src.platform.events import emit
        await emit("thesis.closed", {"thesis_id": thesis.id, "reason": close_reason})
    except Exception:
        pass


class ThesisService:
    """Public API for the thesis domain.

    All business logic lives here or is delegated to specialised helpers:
      ComponentService  — assumption/catalyst/recommendation CRUD
      ThesisRepository  — DB persistence

    Caller is responsible for session lifecycle (commit/rollback).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = ThesisRepository(session)
        self._components = ComponentService(session)

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    async def create(
        self, user_id: str, inp: CreateThesisInput
    ) -> Thesis:
        resolved = _resolve_user_id(user_id)
        target_date = None
        if inp.time_horizon:
            target_date = parse_timeline_to_date(inp.time_horizon)

        thesis = Thesis(
            user_id=resolved,
            ticker=inp.ticker.upper(),
            title=inp.title,
            summary=inp.summary,
            direction=inp.direction,
            target_price=inp.target_price,
            stop_loss=inp.stop_loss,
            entry_price=inp.entry_price,
            status=ThesisStatus.ACTIVE,
        )
        if target_date is not None:
            thesis.target_date = target_date  # type: ignore[attr-defined]

        await self._repo.save(thesis)
        logger.info("thesis.created", thesis_id=thesis.id, ticker=thesis.ticker)
        return thesis

    async def update(
        self, thesis_id: int, user_id: str, inp: UpdateThesisInput
    ) -> Thesis:
        thesis = await self._get_owned(thesis_id, user_id)
        self._assert_mutable(thesis)

        if inp.title is not None:
            thesis.title = inp.title
        if inp.summary is not None:
            thesis.summary = inp.summary
        if inp.target_price is not None:
            thesis.target_price = inp.target_price
        if inp.stop_loss is not None:
            thesis.stop_loss = inp.stop_loss
        if inp.entry_price is not None:
            thesis.entry_price = inp.entry_price
        if inp.direction is not None:
            thesis.direction = inp.direction
        if inp.time_horizon is not None:
            target_date = parse_timeline_to_date(inp.time_horizon)
            thesis.target_date = target_date  # type: ignore[attr-defined]

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
    # Feedback-loop helpers (called by core/feedback_listener.py)
    # ------------------------------------------------------------------

    async def touch_reviewed_at(
        self,
        thesis_id: int,
        user_id: str,
    ) -> Thesis | None:
        """Record that the investor reviewed this thesis (non-destructive).

        Sets Thesis.updated_at via a lightweight update so the readmodel
        knows the thesis was recently reviewed. Does NOT change status,
        score, or any other field.

        Called by:
          core.UserActionFeedbackListener._on_mark_reviewed() for
          MARK_REVIEWED events.

        Args:
            thesis_id: ID of the thesis to touch.
            user_id:   Owner — used for ownership check.

        Returns:
            The updated Thesis, or None if not found / not owned.
        """
        try:
            thesis = await self._get_owned(thesis_id, user_id)
        except ThesisNotFoundError:
            logger.info(
                "thesis.touch_reviewed_at.not_found",
                thesis_id=thesis_id,
                user_id=user_id,
            )
            return None

        # Trigger updated_at refresh via a no-op attribute touch.
        # SQLAlchemy detects the flush and updates the server-side onupdate.
        thesis.updated_at = datetime.now(UTC)  # type: ignore[assignment]
        await self._repo.save(thesis)
        logger.info(
            "thesis.touch_reviewed_at.done",
            thesis_id=thesis_id,
            user_id=user_id,
        )
        return thesis

    async def mark_closed(
        self,
        ticker: str,
        user_id: str,
        *,
        reason: str = "closed",
    ) -> Thesis | None:
        """Close the first ACTIVE thesis for *ticker* owned by *user_id*.

        This is the entry point called by core.FeedbackListener when the
        investor records a SELL action — it avoids the caller needing to
        know the thesis_id.

        Behaviour:
          - Looks up the first ACTIVE thesis for the ticker.
          - Delegates to close() or invalidate() based on *reason*.
          - Returns None (no-op) when no active thesis exists — safe to
            call even if the ticker was never in a thesis.

        Args:
            ticker:  Stock symbol (case-insensitive).
            user_id: Owner.
            reason:  "closed" (default) or "invalidated".

        Returns:
            The updated Thesis, or None if no active thesis was found.
        """
        ticker = ticker.upper()
        theses = await self._repo.list_active_by_ticker(ticker)
        user_theses = [t for t in theses if t.user_id == user_id]
        if not user_theses:
            logger.info(
                "thesis.mark_closed.no_active_thesis",
                ticker=ticker,
                user_id=user_id,
            )
            return None

        thesis = user_theses[0]
        if reason == "invalidated":
            return await self.invalidate(thesis.id, user_id)
        return await self.close(thesis.id, user_id)

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
    # Briefing context helpers
    # ------------------------------------------------------------------

    async def list_active(self, user_id: str | None = None) -> list[Thesis]:
        """Return all ACTIVE theses for a user.

        Called by BriefingService._build_thesis_context().
        """
        return await self.list_for_user(user_id=user_id, status=ThesisStatus.ACTIVE)

    async def get_thesis_health(self, user_id: str | None = None) -> list[dict]:
        """Return health snapshot of all active theses.

        Called by BriefingService._build_thesis_context().
        Returns list of dicts with keys:
          id, ticker, entry_thesis, target_price, stop_loss,
          assumption_count, days_since_review.

        Reads assumptions and reviews from ORM relationships
        (thesis.assumptions, thesis.reviews) — NOT from ComponentService
        which only exposes write/mutation methods.
        Theses must be loaded with selectinload for relationships to be
        available; ThesisRepository.list_by_user uses selectinload by default.
        """
        theses = await self.list_active(user_id=user_id)
        results = []
        for thesis in theses:
            # assumptions: read from ORM relationship
            assumptions = getattr(thesis, "assumptions", None) or []

            # reviews: read from ORM relationship
            reviews = getattr(thesis, "reviews", None) or []

            last_review_at = None
            if reviews:
                valid_dates = [
                    getattr(r, "created_at", None)
                    for r in reviews
                    if getattr(r, "created_at", None) is not None
                ]
                last_review_at = max(valid_dates) if valid_dates else None

            days_since_review = None
            if last_review_at is not None:
                now = datetime.now(UTC)
                if last_review_at.tzinfo is None:
                    last_review_at = last_review_at.replace(tzinfo=UTC)
                days_since_review = (now - last_review_at).days

            results.append({
                "id": thesis.id,
                "ticker": thesis.ticker,
                "entry_thesis": (
                    getattr(thesis, "entry_thesis", None)
                    or getattr(thesis, "summary", "")
                    or ""
                ),
                "target_price": thesis.target_price,
                "stop_loss": thesis.stop_loss,
                # time_horizon is not a DB column — omitted.
                # Use target_date if callers need a deadline reference.
                "target_date": getattr(thesis, "target_date", None),
                "assumption_count": len(assumptions),
                "last_review_at": last_review_at,
                "days_since_review": days_since_review,
            })
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_owned(self, thesis_id: int, user_id: str | None) -> Thesis:
        resolved = _resolve_user_id(user_id)
        thesis = await self._repo.get_by_id(thesis_id)
        if thesis is None:
            raise ThesisNotFoundError(thesis_id)
        if thesis.user_id != resolved:
            raise ThesisNotFoundError(thesis_id)
        return thesis

    @staticmethod
    def _assert_mutable(thesis: Thesis) -> None:
        if thesis.status not in (ThesisStatus.ACTIVE, ThesisStatus.UNDER_REVIEW):
            raise ThesisAlreadyClosedError(thesis.id)
