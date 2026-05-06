"""ThesisReviewListener — Wave 3 stub / Wave 6 full implementation.

Owner: thesis segment.
Boundary:
  - Subscribes to ThesisReviewRequestedEvent on the EventBus.
  - Calls ThesisReviewAgent + ThesisReviewService after receiving event.
  - Emits ThesisInvalidatedEvent if invalidation score crosses threshold.
  - NEVER imports bot/scheduler/Discord internals.

Bootstrap contract::

    listener = ThesisReviewListener(
        thesis_review_agent=...,
        quote_service=...,
    )
    listener.register()   # idempotent
"""
from __future__ import annotations

import logging

from src.platform.event_bus import get_event_bus
from src.platform.events import ThesisInvalidatedEvent, ThesisReviewRequestedEvent

logger = logging.getLogger(__name__)

INVALIDATION_THRESHOLD = 0.75  # score above this triggers ThesisInvalidatedEvent


class ThesisReviewListener:
    """
    Listens for ThesisReviewRequestedEvent, runs AI review, emits
    ThesisInvalidatedEvent if needed.

    Wave 3: stub — logs event, no AI call yet.
    Wave 6: full — pulls thesis from DB, calls ThesisReviewAgent,
            persists ThesisReview, conditionally publishes
            ThesisInvalidatedEvent.
    """

    def __init__(self, thesis_review_agent: object, quote_service: object) -> None:
        self._review_agent = thesis_review_agent
        self._quote_service = quote_service
        self._registered = False

    def register(self) -> None:
        """Subscribe to ThesisReviewRequestedEvent on the global bus. Idempotent."""
        if self._registered:
            return
        bus = get_event_bus()
        bus.subscribe_handler(ThesisReviewRequestedEvent, self._handle_review_requested)
        self._registered = True
        logger.info("thesis_review_listener.registered")

    async def _handle_review_requested(self, event: ThesisReviewRequestedEvent) -> None:
        """
        Wave 3 stub: log and no-op.
        Wave 6 will:
          1. Load Thesis from DB via ThesisRepository
          2. Fetch current quote from QuoteService
          3. Call ThesisReviewAgent.review(thesis, quote_context)
          4. Persist ThesisReview via ThesisReviewService
          5. If invalidation_score >= INVALIDATION_THRESHOLD:
               publish ThesisInvalidatedEvent onto bus
        """
        logger.info(
            "thesis_review_listener.review_requested",
            thesis_id=event.thesis_id,
            symbol=event.symbol,
            reason=event.reason,
            event_id=event.event_id,
        )
        # TODO Wave 6: implement full review + conditional invalidation publish
