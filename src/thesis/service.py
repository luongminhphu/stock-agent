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
from typing import Any

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
    """Fire-and-forget ThesisClosedEvent emission."""
    try:
        from src.platform.event_bus import get_event_bus
        from src.platform.events import ThesisClosedEvent

        event = ThesisClosedEvent(
            thesis_id=thesis.id,
            user_id=thesis.user_id or "",
            ticker=thesis.ticker or "",
            close_reason=close_reason,
            thesis_title=thesis.title or "",
            thesis_summary=thesis.summary or "",
        )
        await get_event_bus().publish(event)
    except Exception as exc:  # noqa: BLE001
        logger.warning("thesis.closed_event.emit_failed", error=str(exc))


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
        self,
        inp: CreateThesisInput,
        user_id: str | None = None,
    ) -> Thesis:
        """Tao thesis moi (kem assumptions/catalysts neu co trong ``inp``).

        ``user_id`` uu tien tham so truyen vao, sau do ``inp.user_id``.
        Bot va API deu goi qua day; adapter khong tu persist component.
        """
        resolved = _resolve_user_id(user_id or inp.user_id)
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

        for desc in inp.assumptions or []:
            await self._components.add_assumption(thesis.id, AddAssumptionInput(description=desc))
        for cat in inp.catalysts or []:
            cat_inp = (
                cat if isinstance(cat, AddCatalystInput) else AddCatalystInput(description=str(cat))
            )
            await self._components.add_catalyst(thesis.id, cat_inp)

        if inp.assumptions or inp.catalysts:
            thesis = await self._get_owned(thesis.id, resolved)  # reload voi components
        return thesis

    async def update(self, thesis_id: int, user_id: str, inp: UpdateThesisInput) -> Thesis:
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

        # Refresh updated_at and last_reviewed_at.
        # last_reviewed_at is the persistent source-of-truth for the Wave 4
        # dedup guard and snapshot stale detection.
        now = datetime.now(UTC)
        thesis.updated_at = now
        thesis.last_reviewed_at = now
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

    async def delete_assumption(self, thesis_id: int, assumption_id: int, user_id: str) -> None:
        await self._get_owned(thesis_id, user_id)
        await self._components.delete_assumption(thesis_id, assumption_id)

    # ------------------------------------------------------------------
    # Catalyst proxy
    # ------------------------------------------------------------------

    async def add_catalyst(self, thesis_id: int, user_id: str, inp: AddCatalystInput) -> Catalyst:
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

    async def delete_catalyst(self, thesis_id: int, catalyst_id: int, user_id: str) -> None:
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

    async def get_thesis_health(
        self,
        user_id: str | None = None,
        *,
        quote_service: object | None = None,
        ticker_context_service: object | None = None,
    ) -> list[dict[str, Any]]:
        """Return health snapshot of all active theses (dict form).

        Called by BriefingService._build_thesis_context().

        Wave D4: delegate sang ``thesis.health_snapshot.build_thesis_health_snapshots``
        — cùng projection với ai.context_builder, không nhân đôi rule. Truyền
        quote_service / ticker_context_service để có giá + khoảng cách stop.

        Keys giữ tương thích: id, ticker, entry_thesis, target_price, stop_loss,
        target_date, assumption_count, last_review_at, days_since_review.
        Keys mới: status (urgency_flag), health_score (0–1), last_verdict,
        current_price, distance_to_stop_pct, stop_distance_atr, stop_proximity,
        near_stop, price_quality, title, direction, assumptions_invalidated.
        """
        from src.thesis.health_snapshot import build_thesis_health_snapshots

        active = await self.list_active(user_id=user_id)
        snapshots = await build_thesis_health_snapshots(
            self._session,
            user_id=_resolve_user_id(user_id),
            ticker_context_service=ticker_context_service,
            quote_service=quote_service,
            max_theses=10_000,  # briefing cần toàn bộ, không cap theo prompt
            theses=active,
        )
        theses = {str(t.id): t for t in active}
        results: list[dict[str, Any]] = []
        for snap in snapshots:
            thesis = theses.get(snap.thesis_id)
            last_review_at = None
            if thesis is not None and thesis.last_reviewed_at is not None:
                last_review_at = thesis.last_reviewed_at
                if last_review_at.tzinfo is None:
                    last_review_at = last_review_at.replace(tzinfo=UTC)
            results.append(
                {
                    "id": thesis.id if thesis is not None else snap.thesis_id,
                    "ticker": snap.ticker,
                    "title": snap.title,
                    "direction": snap.direction,
                    "entry_thesis": (getattr(thesis, "summary", None) or "") if thesis else "",
                    "target_price": snap.target_price,
                    "stop_loss": snap.stop_loss,
                    "target_date": getattr(thesis, "target_date", None),
                    "assumption_count": snap.assumptions_total,
                    "assumptions_invalidated": snap.assumptions_invalidated,
                    "last_review_at": last_review_at,
                    "days_since_review": (
                        snap.days_since_review if snap.days_since_review < 999 else None
                    ),
                    "status": snap.urgency_flag,
                    "health_score": snap.health_score,
                    "last_verdict": snap.last_verdict,
                    "current_price": snap.current_price,
                    "distance_to_stop_pct": snap.distance_to_stop_pct,
                    "stop_distance_atr": snap.stop_distance_atr,
                    "stop_proximity": snap.stop_proximity,
                    "near_stop": snap.near_stop,
                    "price_quality": snap.price_quality,
                }
            )
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
        # UNDER_REVIEW was removed from ThesisStatus — mutable states are ACTIVE and WEAKENING.
        # PAUSED is intentionally excluded: paused thesis should not be modified until resumed.
        if thesis.status not in (ThesisStatus.ACTIVE, ThesisStatus.WEAKENING):
            raise ThesisAlreadyClosedError(thesis.id)
