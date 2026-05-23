"""
EngineFeedbackListener — EventBus subscriber that persists verdict feedback.

Owner: core segment.

Consumed event : EngineFeedbackSubmittedEvent
                 (produced by: bot.FeedbackCommandHandler | api endpoint)

Pattern follows repo convention:
    __init__(bus) → register() → bus.subscribe() → async _handle()

This is a thin adapter. No domain logic here.
All persistence is delegated to FeedbackStore.

Boot: call EngineFeedbackListener().register() in platform bootstrap.
"""
from __future__ import annotations

from src.core.feedback import FeedbackStore
from src.platform.event_bus import EventBus, get_event_bus
from src.platform.events import EngineFeedbackSubmittedEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)


class EngineFeedbackListener:
    """Subscribe to EngineFeedbackSubmittedEvent → persist via FeedbackStore."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self._bus = bus or get_event_bus()

    def register(self) -> None:
        self._bus.subscribe(EngineFeedbackSubmittedEvent, self._handle)
        logger.info("feedback_listener.registered")

    async def _handle(self, event: EngineFeedbackSubmittedEvent) -> None:
        logger.info(
            "feedback_listener.received",
            verdict_event_id=event.verdict_event_id,
            outcome=event.outcome,
            user_id=event.user_id,
        )
        try:
            await FeedbackStore.record(
                verdict_event_id=event.verdict_event_id,
                user_id=event.user_id,
                verdict=event.verdict,
                outcome=event.outcome,
                trigger_source=event.trigger_source,
                user_note=event.user_note or None,
            )
        except Exception as exc:
            # Feedback failure must never crash the bus
            logger.error(
                "feedback_listener.record_failed",
                verdict_event_id=event.verdict_event_id,
                error=str(exc),
            )
