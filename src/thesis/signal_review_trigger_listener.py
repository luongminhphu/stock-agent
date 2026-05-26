"""
SignalReviewTriggerListener — Thesis Segment

Subscribes to ThesisReviewTriggeredEvent emitted by ai.SignalEngineListener.
Resolves thesis_id + user_id from DB (by thesis_id or ticker fallback),
then calls ReviewService.review_thesis() for each trigger that passes
the in-memory dedup guard.

Owner: thesis segment. Adapter only — no AI logic here.
AI + domain logic lives in review_service.py + ThesisReviewAgent.

Boundary:
  - KHÔNG gọi SignalEngineAgent trực tiếp.
  - KHÔNG chứa thesis domain rule (valid/invalid/scoring).
  - KHÔNG emit downstream events — ReviewService handles persistence.
  - bot và api không gọi listener này trực tiếp.

Wire-up: call SignalReviewTriggerListener(...).register() during app bootstrap.

  listener = SignalReviewTriggerListener(
      session_factory=session_factory,
      review_agent=review_agent,
      quote_service=quote_service,   # optional
  )
  listener.register()

Dedup strategy:
  In-memory set of (thesis_id, phase) pairs per process lifetime.
  Prevents the same thesis being reviewed twice in one signal engine run.
  Set is cleared on each new SignalEngineCompletedEvent to allow
  re-triggering across different runs.
"""
from __future__ import annotations

from typing import Any

from src.platform.event_bus import get_event_bus
from src.platform.events import (
    SignalEngineCompletedEvent,
    ThesisReviewTriggeredEvent,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)


class SignalReviewTriggerListener:
    """Listens for ThesisReviewTriggeredEvent, calls ReviewService.review_thesis().

    Lifecycle::

        listener = SignalReviewTriggerListener(
            session_factory=session_factory,
            review_agent=review_agent,
            quote_service=quote_service,  # optional
        )
        listener.register()  # call once in on_ready / app bootstrap

    Dependencies:
        session_factory — async context manager producing AsyncSession
        review_agent    — ThesisReviewAgent singleton
        quote_service   — optional QuoteReader for live price enrichment
    """

    def __init__(
        self,
        session_factory: Any,
        review_agent: Any,
        quote_service: Any = None,
    ) -> None:
        self._session_factory = session_factory
        self._review_agent = review_agent
        self._quote_service = quote_service
        self._registered = False
        # Dedup: (thesis_id_str, phase) — cleared on each SignalEngineCompletedEvent
        self._seen_this_run: set[tuple[str, str]] = set()

    def register(self) -> None:
        """Subscribe to ThesisReviewTriggeredEvent and SignalEngineCompletedEvent.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._registered:
            logger.warning("SignalReviewTriggerListener already registered — skipping.")
            return
        bus = get_event_bus()
        bus.subscribe_handler(ThesisReviewTriggeredEvent, self._handle_trigger)
        bus.subscribe_handler(SignalEngineCompletedEvent, self._reset_dedup)
        self._registered = True
        logger.info("SignalReviewTriggerListener registered on event bus.")

    # ── internal ───────────────────────────────────────────────────────────

    async def _reset_dedup(self, _event: SignalEngineCompletedEvent) -> None:
        """Clear per-run dedup set at the start of each new signal engine run.

        SignalEngineCompletedEvent fires *after* all ThesisReviewTriggeredEvents
        from the same run — dedup is effective within a single run, then cleared
        so subsequent runs can re-trigger reviews correctly.
        """
        self._seen_this_run.clear()

    async def _handle_trigger(self, event: ThesisReviewTriggeredEvent) -> None:
        """Handle one ThesisReviewTriggeredEvent.

        1. Resolve thesis_id + user_id: by event.thesis_id if non-empty,
           else by ticker + user_id via ThesisRepository ticker fallback.
        2. Dedup guard: skip if (thesis_id, phase) already seen this run.
        3. Call ReviewService.review_thesis() — handles load, AI, persist.
        """
        logger.info(
            "signal_review_trigger_listener.received",
            thesis_id=event.thesis_id,
            ticker=event.ticker,
            urgency=event.urgency,
            phase=event.phase,
        )

        # -- 1. Resolve thesis_id + user_id ------------------------------
        resolved = await self._resolve_thesis_id(event)
        if resolved is None:
            logger.warning(
                "signal_review_trigger_listener.thesis_not_found",
                thesis_id=event.thesis_id,
                ticker=event.ticker,
                user_id=event.user_id,
            )
            return

        thesis_id_int, user_id = resolved

        # -- 2. Dedup guard ----------------------------------------------
        dedup_key = (str(thesis_id_int), event.phase)
        if dedup_key in self._seen_this_run:
            logger.debug(
                "signal_review_trigger_listener.dedup_skip",
                thesis_id=thesis_id_int,
                phase=event.phase,
            )
            return
        self._seen_this_run.add(dedup_key)

        # -- 3. Call ReviewService.review_thesis() -----------------------
        try:
            from src.thesis.review_service import ReviewNotAllowedError, ReviewService
            from src.thesis.service import ThesisNotFoundError

            async with self._session_factory() as session:
                svc = ReviewService(
                    session=session,
                    agent=self._review_agent,
                    quote_service=self._quote_service,
                )
                await svc.review_thesis(
                    thesis_id=thesis_id_int,
                    user_id=user_id,
                )

            logger.info(
                "signal_review_trigger_listener.review_done",
                thesis_id=thesis_id_int,
                ticker=event.ticker,
                urgency=event.urgency,
            )
        except ReviewNotAllowedError as exc:
            logger.info(
                "signal_review_trigger_listener.review_not_allowed",
                thesis_id=thesis_id_int,
                reason=str(exc),
            )
        except ThesisNotFoundError as exc:
            logger.warning(
                "signal_review_trigger_listener.thesis_not_found_at_review",
                thesis_id=thesis_id_int,
                reason=str(exc),
            )
        except Exception as exc:
            logger.exception(
                "signal_review_trigger_listener.review_failed",
                thesis_id=thesis_id_int,
                error=str(exc),
            )

    async def _resolve_thesis_id(
        self, event: ThesisReviewTriggeredEvent
    ) -> tuple[int, str] | None:
        """Resolve (thesis_id: int, user_id: str) needed by ReviewService.

        Primary path:  event.thesis_id non-empty → parse as int directly.
        Fallback path: ticker + user_id → query ThesisRepository for first
                       active thesis matching that ticker.

        Returns None if neither path yields a valid result.
        """
        # Primary: thesis_id already known from signal output
        if event.thesis_id:
            try:
                return int(event.thesis_id), event.user_id
            except (ValueError, TypeError):
                logger.warning(
                    "signal_review_trigger_listener.invalid_thesis_id",
                    thesis_id=event.thesis_id,
                )

        # Fallback: resolve by ticker + user_id via DB
        if event.ticker and event.user_id:
            try:
                from src.thesis.repository import ThesisRepository
                from src.thesis.models import ThesisStatus

                async with self._session_factory() as session:
                    repo = ThesisRepository(session)
                    theses = await repo.list_active_for_user(user_id=event.user_id)
                    match = next(
                        (t for t in theses if t.ticker.upper() == event.ticker.upper()),
                        None,
                    )
                    if match is not None:
                        return match.id, event.user_id
            except Exception as exc:
                logger.warning(
                    "signal_review_trigger_listener.ticker_fallback_failed",
                    ticker=event.ticker,
                    user_id=event.user_id,
                    error=str(exc),
                )

        return None
