"""
ThesisReviewListener — Wave 6 full implementation.

Owner: thesis segment.
Boundary:
  - Subscribes to ThesisReviewRequestedEvent on the global EventBus.
  - Delegates AI review to ReviewService (single source of truth).
  - Publishes ThesisInvalidatedEvent when invalidation_score >= threshold.
  - NEVER imports bot / scheduler / Discord internals.
  - NEVER holds an open AsyncSession across events — uses session_factory.

Bootstrap contract::

    listener = ThesisReviewListener(
        session_factory=async_session_factory,
        review_agent=thesis_review_agent_singleton,
        quote_service=quote_service_singleton,   # optional
    )
    listener.register()   # idempotent
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.event_bus import get_event_bus
from src.platform.events import ThesisInvalidatedEvent, ThesisReviewRequestedEvent

logger = logging.getLogger(__name__)

# Score above this threshold triggers ThesisInvalidatedEvent.
INVALIDATION_THRESHOLD = 0.75

# ReviewVerdict string values that map to high invalidation scores.
_HIGH_INVALIDATION_VERDICTS = {"INVALIDATED", "BEARISH"}   # ThesisReviewOutput.verdict
_MED_INVALIDATION_VERDICTS  = {"WEAKENING", "NEUTRAL"}     # partial concern


def _verdict_to_invalidation_score(verdict: str, confidence: float) -> float:
    """
    Heuristic mapping ReviewVerdict → invalidation_score [0.0 – 1.0].

    The score represents "how strongly AI believes the thesis is broken".
    confidence acts as a multiplier so a low-confidence INVALIDATED verdict
    still triggers, but a very low-confidence BEARISH verdict may not.

    Scale:
        INVALIDATED  → 0.90 * confidence  (nearly always above threshold)
        BEARISH      → 0.82 * confidence
        WEAKENING    → 0.65 * confidence  (below threshold by default)
        NEUTRAL      → 0.40 * confidence
        BULLISH      → 0.15 * confidence  (thesis healthy)
        <unknown>    → 0.50 * confidence  (safe fallback)
    """
    base: dict[str, float] = {
        "INVALIDATED": 0.90,
        "BEARISH":     0.82,
        "WEAKENING":   0.65,
        "NEUTRAL":     0.40,
        "BULLISH":     0.15,
    }
    raw = base.get(verdict.upper(), 0.50)
    return round(raw * max(0.0, min(1.0, confidence)), 4)


AsyncSessionFactory = Callable[[], AsyncGenerator[AsyncSession, None]]


class ThesisReviewListener:
    """
    Full Wave 6 implementation of the thesis review event listener.

    Flow per event
    --------------
    1. _load_thesis()          — verify thesis exists and is ACTIVE
    2. _run_review()           — delegate to ReviewService (AI + persist + scoring)
    3. _maybe_invalidate()     — publish ThesisInvalidatedEvent if score >= threshold
    4. (implicit cleanup)      — session closed by context manager

    Fault isolation
    ---------------
    Each step is a separate try/except.  A failure in step 2 skips steps 3–4
    and logs the error — the bus worker still marks the event done, preventing
    infinite requeue.
    """

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        review_agent: Any,
        quote_service: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._review_agent = review_agent
        self._quote_service = quote_service
        self._registered = False

    # ── bootstrap ────────────────────────────────────────────────────────────

    def register(self) -> None:
        """Subscribe to ThesisReviewRequestedEvent on the global bus. Idempotent."""
        if self._registered:
            return
        bus = get_event_bus()
        bus.subscribe_handler(ThesisReviewRequestedEvent, self._handle_review_requested)
        self._registered = True
        logger.info("thesis_review_listener.registered")

    # ── handler ───────────────────────────────────────────────────────────────

    async def _handle_review_requested(self, event: ThesisReviewRequestedEvent) -> None:
        """
        Main handler — receives ThesisReviewRequestedEvent from the event bus.
        """
        logger.info(
            "thesis_review_listener.received",
            extra={
                "thesis_id": event.thesis_id,
                "symbol":    event.symbol,
                "reason":    event.reason,
                "event_id":  event.event_id,
            },
        )

        review = await self._run_review(event)
        if review is None:
            return  # AI/DB failure already logged inside _run_review

        await self._maybe_invalidate(event, review)

    # ── step 2: run review ────────────────────────────────────────────────────

    async def _run_review(self, event: ThesisReviewRequestedEvent) -> Any | None:
        """
        Open a fresh session, build ReviewService, call review_thesis().

        session_factory is forwarded into ReviewService so ReviewOutcomeReactor
        runs in the same session after every review (Wave 3 activation).

        Returns ThesisReview ORM instance on success, None on any failure.
        Session is always closed after this call — no leak across events.
        """
        from src.thesis.review_service import ReviewService, ReviewNotAllowedError
        from src.thesis.service import ThesisNotFoundError

        try:
            thesis_id_int = int(event.thesis_id)
        except (ValueError, TypeError):
            logger.error(
                "thesis_review_listener.invalid_thesis_id",
                extra={"thesis_id": event.thesis_id, "event_id": event.event_id},
            )
            return None

        async with self._open_session() as session:
            service = ReviewService(
                session=session,
                agent=self._review_agent,
                quote_service=self._quote_service,
                # Wave 3: forward session_factory so ReviewOutcomeReactor activates
                # after each review — mutates WatchlistItem + creates THESIS_TRIGGER alerts.
                session_factory=self._session_factory,
            )
            try:
                from src.thesis.repository import ThesisRepository
                repo = ThesisRepository(session)
                thesis = await repo.get_by_id(thesis_id_int)
                if thesis is None:
                    logger.warning(
                        "thesis_review_listener.thesis_not_found",
                        extra={"thesis_id": thesis_id_int, "event_id": event.event_id},
                    )
                    return None

                review = await service.review_thesis(
                    thesis_id=thesis_id_int,
                    user_id=thesis.user_id,
                )
                logger.info(
                    "thesis_review_listener.review_done",
                    extra={
                        "thesis_id":  thesis_id_int,
                        "verdict":    review.verdict,
                        "confidence": review.confidence,
                        "event_id":   event.event_id,
                    },
                )
                return review

            except ReviewNotAllowedError as exc:
                logger.info(
                    "thesis_review_listener.review_skipped",
                    extra={
                        "thesis_id": thesis_id_int,
                        "reason":    str(exc),
                        "event_id":  event.event_id,
                    },
                )
                return None

            except Exception as exc:
                logger.exception(
                    "thesis_review_listener.review_failed",
                    extra={
                        "thesis_id": thesis_id_int,
                        "error":     str(exc),
                        "event_id":  event.event_id,
                    },
                )
                return None

    # ── step 3: conditional invalidation ─────────────────────────────────────

    async def _maybe_invalidate(
        self,
        event: ThesisReviewRequestedEvent,
        review: Any,
    ) -> None:
        """
        Compute invalidation_score from (verdict, confidence).
        If score >= INVALIDATION_THRESHOLD → publish ThesisInvalidatedEvent.

        Uses dedup_key=thesis_id so the same thesis won't be re-invalidated
        within the EventBus default dedup window (60 min).
        """
        verdict_str: str = (
            review.verdict.value
            if hasattr(review.verdict, "value")
            else str(review.verdict)
        )
        confidence: float = float(review.confidence or 0.0)
        invalidation_score = _verdict_to_invalidation_score(verdict_str, confidence)

        logger.info(
            "thesis_review_listener.invalidation_score",
            extra={
                "thesis_id":         event.thesis_id,
                "verdict":           verdict_str,
                "confidence":        confidence,
                "invalidation_score": invalidation_score,
                "threshold":         INVALIDATION_THRESHOLD,
            },
        )

        if invalidation_score < INVALIDATION_THRESHOLD:
            return

        trigger_description = (
            (review.reasoning or "")[:200].strip()
            or f"AI verdict: {verdict_str} (score={invalidation_score})"
        )

        invalidated_event = ThesisInvalidatedEvent(
            thesis_id=event.thesis_id,
            symbol=event.symbol,
            trigger_description=trigger_description,
            invalidation_score=invalidation_score,
        )

        bus = get_event_bus()
        published = await bus.publish(
            invalidated_event,
            dedup_key=f"invalidated:{event.thesis_id}",
        )

        if published:
            logger.warning(
                "thesis_review_listener.invalidation_published",
                extra={
                    "thesis_id":         event.thesis_id,
                    "symbol":            event.symbol,
                    "invalidation_score": invalidation_score,
                    "verdict":           verdict_str,
                    "event_id":          invalidated_event.event_id,
                },
            )
        else:
            logger.info(
                "thesis_review_listener.invalidation_dedup_suppressed",
                extra={"thesis_id": event.thesis_id},
            )

    # ── helpers ───────────────────────────────────────────────────────────────

    @asynccontextmanager
    async def _open_session(self):
        """Open a fresh AsyncSession from the factory. Always closes on exit."""
        async with self._session_factory() as session:
            yield session
