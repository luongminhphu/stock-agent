"""ProactiveAlertAgent — Wave 3 stub / Wave 5 full implementation.

Owner: ai segment.
Boundary:
  - Subscribes to SignalDetectedEvent on the EventBus.
  - After analysis, emits RecommendationReadyEvent back onto the bus.
  - NEVER imports Discord, bot, or scheduler internals.
  - NEVER writes to signal_events directly — that is watchlist segment's job.

Bootstrap contract (enforced by bootstrap.py)::

    agent = get_proactive_alert_agent(ai_client=...)
    agent.register()   # subscribes handler on bus; idempotent
"""
from __future__ import annotations

import logging

from src.platform.event_bus import get_event_bus
from src.platform.events import RecommendationReadyEvent, SignalDetectedEvent

logger = logging.getLogger(__name__)

_instance: "ProactiveAlertAgent | None" = None


class ProactiveAlertAgent:
    """
    Listens for SignalDetectedEvent and produces RecommendationReadyEvent.

    Wave 3: stub — logs signal, no AI call yet.
    Wave 5: full — calls AIClient, builds verdict + risk signals, publishes
            RecommendationReadyEvent with action/urgency/confidence.
    """

    def __init__(self, ai_client: object) -> None:
        self._ai_client = ai_client
        self._registered = False

    def register(self) -> None:
        """Subscribe to SignalDetectedEvent on the global bus. Idempotent."""
        if self._registered:
            return
        bus = get_event_bus()
        bus.subscribe_handler(SignalDetectedEvent, self._handle_signal)
        self._registered = True
        logger.info("proactive_alert_agent.registered")

    async def _handle_signal(self, event: SignalDetectedEvent) -> None:
        """
        Wave 3 stub: log and no-op.
        Wave 5 will:
          1. Pull context: quote, thesis, recent signals from WatchlistRepository
          2. Call AIClient with ProactiveAlertPrompt
          3. Parse ProactiveRecommendation schema
          4. Publish RecommendationReadyEvent onto bus
          5. Mark signal_event.processed_at via WatchlistRepository
        """
        logger.info(
            "proactive_alert_agent.signal_received",
            symbol=event.symbol,
            signal_type=event.signal_type,
            strength=event.strength,
            confidence=event.confidence,
            event_id=event.event_id,
        )
        # TODO Wave 5: implement AI analysis + publish RecommendationReadyEvent


def get_proactive_alert_agent(ai_client: object) -> ProactiveAlertAgent:
    """Return singleton ProactiveAlertAgent. Creates on first call."""
    global _instance
    if _instance is None:
        _instance = ProactiveAlertAgent(ai_client=ai_client)
    return _instance


def reset_proactive_alert_agent() -> None:
    """Reset singleton — for tests only."""
    global _instance
    _instance = None
