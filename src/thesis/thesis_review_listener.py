"""Thesis Review Listener — Wave 6.

Owner: thesis segment.

Subscribes to ThesisReviewRequestedEvent (emitted by ProactiveAlertAgent
when a signal is detected for a symbol that has an active thesis).

Flow:
    1. Receive ThesisReviewRequestedEvent(thesis_id, symbol, reason)
    2. Open a short-lived DB session
    3. Look up the active thesis — skip if not found or not ACTIVE
    4. Delegate to ReviewService.review_thesis()
    5. If verdict == INVALIDATED → emit ThesisInvalidatedEvent on bus
    6. Log outcome at INFO level

Dedup:
    dedup_key = f"{symbol}:thesis_review"
    window    = 4 hours  (configurable via THESIS_REVIEW_DEDUP_HOURS env)
    Prevents AI spam when multiple signals fire for the same symbol
    within a short window (e.g., breakout + news in the same session).

Boundary:
    - This file ONLY wires the event contract → ReviewService.
    - All domain logic lives in ReviewService / ThesisRepository.
    - DB session is opened and closed here; never held across the listener.
    - Does NOT touch bot / API / Discord — those layers react to
      ThesisInvalidatedEvent independently.
"""

from __future__ import annotations

from datetime import timedelta

from src.platform.db import AsyncSessionLocal
from src.platform.event_bus import get_event_bus
from src.platform.events import (
    ThesisInvalidatedEvent,
    ThesisReviewRequestedEvent,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

_DEDUP_HOURS = 4  # overrideable in tests via module attribute


class ThesisReviewListener:
    """Event-driven bridge: ThesisReviewRequestedEvent → ReviewService.

    Instantiated once at bootstrap and registered on the global EventBus.
    Requires:
        - thesis_review_agent   (ThesisReviewAgent singleton from bootstrap)
        - quote_service         (QuoteService singleton from bootstrap, optional)
    """

    def __init__(
        self,
        thesis_review_agent,  # ThesisReviewAgent — injected to avoid circular import
        quote_service=None,   # QuoteReader | None
    ) -> None:
        self._agent = thesis_review_agent
        self._quote_service = quote_service

    def register(self) -> None:
        """Subscribe handle() on the global EventBus."""
        bus = get_event_bus()
        bus.subscribe_handler(ThesisReviewRequestedEvent, self.handle)
        logger.info("thesis_review_listener.registered")

    async def handle(self, event: ThesisReviewRequestedEvent) -> None:  # noqa: C901
        """Handle a ThesisReviewRequestedEvent.

        This is the hot path — called by the EventBus worker on every
        ThesisReviewRequestedEvent. Failures are caught so the bus worker
        is never blocked; errors go to the dead-letter queue via exception
        propagation back to EventBus._dispatch.
        """
        logger.info(
            "thesis_review_listener.received",
            thesis_id=event.thesis_id,
            symbol=event.symbol,
            reason=event.reason,
            event_id=event.event_id,
        )

        bus = get_event_bus()

        async with AsyncSessionLocal() as session:
            # Import here to avoid circular imports at module level
            from src.thesis.models import ThesisStatus
            from src.thesis.repository import ThesisRepository
            from src.thesis.review_service import ReviewNotAllowedError, ReviewService
            from src.thesis.service import ThesisNotFoundError

            repo = ThesisRepository(session)

            # Guard: thesis must still be ACTIVE (could have been closed since event was emitted)
            thesis = await repo.get_by_id(int(event.thesis_id))
            if thesis is None:
                logger.warning(
                    "thesis_review_listener.thesis_not_found",
                    thesis_id=event.thesis_id,
                    symbol=event.symbol,
                )
                return

            if thesis.status != ThesisStatus.ACTIVE:
                logger.info(
                    "thesis_review_listener.skipped_non_active",
                    thesis_id=event.thesis_id,
                    status=str(thesis.status),
                )
                return

            review_svc = ReviewService(
                session=session,
                agent=self._agent,
                quote_service=self._quote_service,
            )

            try:
                review = await review_svc.review_thesis(
                    thesis_id=int(event.thesis_id),
                    user_id=thesis.user_id,
                )
                await session.commit()
            except (ThesisNotFoundError, ReviewNotAllowedError) as exc:
                logger.warning(
                    "thesis_review_listener.review_skipped",
                    thesis_id=event.thesis_id,
                    reason=str(exc),
                )
                return
            except Exception as exc:
                logger.exception(
                    "thesis_review_listener.review_failed",
                    thesis_id=event.thesis_id,
                    symbol=event.symbol,
                    error=str(exc),
                )
                raise  # re-raise → EventBus dead-letter queue

        logger.info(
            "thesis_review_listener.review_done",
            thesis_id=event.thesis_id,
            symbol=event.symbol,
            verdict=str(review.verdict),
            confidence=review.confidence,
        )

        # Emit ThesisInvalidatedEvent if AI verdict is INVALIDATED
        from src.thesis.models import ReviewVerdict

        if review.verdict == ReviewVerdict.INVALIDATED:
            invalidated_evt = ThesisInvalidatedEvent(
                thesis_id=str(event.thesis_id),
                symbol=event.symbol,
                trigger_description=(
                    f"Signal-driven review ({event.reason}): "
                    f"verdict=INVALIDATED, confidence={review.confidence:.2f}"
                ),
                invalidation_score=review.confidence,
            )
            await bus.publish(
                invalidated_evt,
                dedup_key=f"{event.symbol}:invalidated",
                dedup_window=timedelta(hours=_DEDUP_HOURS),
            )
            logger.warning(
                "thesis_review_listener.thesis_invalidated",
                thesis_id=event.thesis_id,
                symbol=event.symbol,
                confidence=review.confidence,
            )
