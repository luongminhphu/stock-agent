"""Opportunity Screen Subscriber — market segment.

Owner: market segment (event subscriber adapter).

Listens for OpportunityScreenCompletedEvent emitted by
run_opportunity_screen_job() after each market screen run.

Responsibilities:
    - Receive OpportunityScreenCompletedEvent from EventBus.
    - Resolve scheduler_user_id from settings (single-user mode).
    - Emit OpportunityAIAnalysisRequestedEvent → ai segment handler.

Non-responsibilities:
    - Does NOT call AI directly — that belongs in ai segment.
    - Does NOT persist candidates — they are ephemeral screen results.
    - Does NOT send Discord messages — bot adapter handles delivery.

Lifecycle:
    subscriber = OpportunityScreenSubscriber()
    subscriber.register()   ← called in bootstrap(), after bus.start()
"""
from __future__ import annotations

from src.platform.logging import get_logger

logger = get_logger(__name__)


class OpportunityScreenSubscriber:
    """Subscribe to OpportunityScreenCompletedEvent and emit AI request."""

    def register(self) -> None:
        """Register handler on the EventBus. Safe to call multiple times."""
        from src.platform.event_bus import get_event_bus
        from src.platform.events import OpportunityScreenCompletedEvent

        bus = get_event_bus()
        bus.subscribe_handler(OpportunityScreenCompletedEvent, self._handle)
        logger.info("opportunity_screen_subscriber.registered")

    async def _handle(self, event: object) -> None:
        """Handle OpportunityScreenCompletedEvent → emit AI analysis request.

        Emits OpportunityAIAnalysisRequestedEvent with candidates_payload
        so the ai.OpportunityAnalysisHandler can cross-check against
        the investor’s watchlist and active theses without re-fetching
        market data.

        Failure contract: any error is caught and logged as WARNING.
        Never raises — screen pipeline is never blocked.
        """
        candidates_found: int = getattr(event, "candidates_found", 0)
        top_symbol: str = getattr(event, "top_symbol", "")
        screen_criteria: str = getattr(event, "screen_criteria", "")
        candidates_payload: tuple[str, ...] = getattr(event, "candidates_payload", ())
        trading_date: str = getattr(event, "trading_date", "")

        logger.info(
            "opportunity_screen_subscriber.received",
            candidates_found=candidates_found,
            top_symbol=top_symbol,
            screen_criteria=screen_criteria,
        )

        if candidates_found == 0:
            logger.debug("opportunity_screen_subscriber.no_candidates_skip")
            return

        try:
            from src.platform.config import settings
            from src.platform.event_bus import get_event_bus
            from src.platform.events import OpportunityAIAnalysisRequestedEvent

            user_id = getattr(settings, "scheduler_user_id", "") or ""
            if not user_id:
                logger.warning(
                    "opportunity_screen_subscriber.no_user_id",
                    hint="Set SCHEDULER_USER_ID in .env to enable AI cross-check",
                )
                return

            ai_event = OpportunityAIAnalysisRequestedEvent(
                user_id=str(user_id),
                candidates_payload=candidates_payload,
                screen_criteria=screen_criteria,
                trading_date=trading_date,
                top_symbol=top_symbol,
            )
            await get_event_bus().publish(ai_event)
            logger.info(
                "opportunity_screen_subscriber.ai_request_emitted",
                user_id=str(user_id),
                candidates_count=len(candidates_payload),
                top_symbol=top_symbol,
            )
        except Exception as exc:
            logger.warning(
                "opportunity_screen_subscriber.ai_request_failed",
                error=str(exc),
            )
