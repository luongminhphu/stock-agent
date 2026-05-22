"""Opportunity Screen Subscriber — market segment, Wave 3.

Owner: market segment (event subscriber adapter).

Listens for OpportunityScreenCompletedEvent emitted by
run_opportunity_screen_job() after each market screen run.

Responsibilities:
    - Receive OpportunityScreenCompletedEvent from EventBus.
    - Log receipt with candidate metadata for observability.
    - (Wave 3 TODO) Trigger AI analysis of top candidates:
        * Cross-check candidates against user watchlist + active theses.
        * Emit OpportunityAIAnalysisRequestedEvent → ai segment handler.
        * ai segment returns ranked, context-aware opportunity summary.

Non-responsibilities:
    - Does NOT call AI directly — that belongs in ai segment.
    - Does NOT persist candidates — they are ephemeral screen results.
    - Does NOT send Discord messages — bot/briefing adapters handle delivery.

Lifecycle:
    subscriber = OpportunityScreenSubscriber()
    subscriber.register()   ← called in bootstrap(), after bus.start()
"""
from __future__ import annotations

from src.platform.logging import get_logger

logger = get_logger(__name__)


class OpportunityScreenSubscriber:
    """Subscribe to OpportunityScreenCompletedEvent and route to AI analysis.

    Wave 3 stub: logs receipt and provides the hook point for downstream
    AI cross-check against watchlist + thesis context.
    """

    def register(self) -> None:
        """Register handler on the EventBus. Safe to call multiple times."""
        from src.platform.event_bus import get_event_bus
        from src.platform.events import OpportunityScreenCompletedEvent

        bus = get_event_bus()
        bus.subscribe(OpportunityScreenCompletedEvent, self._handle)
        logger.info("opportunity_screen_subscriber.registered")

    async def _handle(self, event: object) -> None:
        """Handle OpportunityScreenCompletedEvent.

        Current behaviour (stub):
            - Log event metadata for observability.

        Wave 3 TODO:
            - Fetch user watchlist tickers from watchlist segment.
            - Cross-check top candidates against active theses.
            - Emit OpportunityAIAnalysisRequestedEvent for ai segment to
              produce a ranked, context-aware opportunity narrative.
        """
        candidates_found: int = getattr(event, "candidates_found", 0)
        top_symbol: str = getattr(event, "top_symbol", "")
        screen_criteria: str = getattr(event, "screen_criteria", "")

        logger.info(
            "opportunity_screen_subscriber.received",
            candidates_found=candidates_found,
            top_symbol=top_symbol,
            screen_criteria=screen_criteria,
        )

        if candidates_found == 0:
            logger.debug("opportunity_screen_subscriber.no_candidates_skip")
            return

        # Wave 3 TODO: emit OpportunityAIAnalysisRequestedEvent so ai segment
        # can cross-check candidates against watchlist + active theses.
        logger.debug(
            "opportunity_screen_subscriber.ai_hook_pending",
            note="Wave 3: AI cross-check not yet implemented",
        )
