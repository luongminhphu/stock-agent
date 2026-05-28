"""GlobalRiskSubscriber — listens to IntelligenceEngineCompletedEvent.

Owner: readmodel segment.

When IntelligenceEngine completes a run and emits
IntelligenceEngineCompletedEvent, this subscriber updates
GlobalRiskStore with the latest EngineVerdict so downstream
capabilities (briefing, watchlist scan, thesis maintenance) can
read flagged tickers without DB queries.

Registration: platform/bootstrap.py — GlobalRiskSubscriber.register()

Pattern: matches CacheSubscriber — module-level _registered guard,
@staticmethod register(), handlers defined as closures inside register().

Non-blocking: any error inside the handler is logged and swallowed —
never interrupts the engine run or the event bus delivery chain.
"""

from __future__ import annotations

from src.platform.event_bus import get_event_bus
from src.platform.events import IntelligenceEngineCompletedEvent
from src.platform.logging import get_logger
from src.readmodel.global_risk_store import get_global_risk_store

logger = get_logger(__name__)

_registered = False


class GlobalRiskSubscriber:
    """Registers IntelligenceEngineCompletedEvent handler on the global EventBus
    to keep GlobalRiskStore in sync after every engine cycle.
    """

    @staticmethod
    def register() -> None:
        """Wire GlobalRiskStore update handler onto the global EventBus.

        Idempotent — safe to call multiple times (e.g. in tests).
        Only the first call registers; subsequent calls are no-ops.
        """
        global _registered
        if _registered:
            logger.debug("GlobalRiskSubscriber.register() skipped — already registered")
            return

        bus = get_event_bus()

        @bus.subscribe(IntelligenceEngineCompletedEvent)
        async def _on_engine_completed(event: IntelligenceEngineCompletedEvent) -> None:
            try:
                user_id: str = event.user_id or ""
                if not user_id:
                    logger.warning(
                        "global_risk_subscriber.missing_user_id",
                        event_type=type(event).__name__,
                    )
                    return

                store = get_global_risk_store()
                store.update(user_id, event)

                logger.debug(
                    "global_risk_subscriber.handled",
                    user_id=user_id,
                    verdict=event.verdict,
                    flagged_count=len(store.get_flagged_tickers(user_id)),
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "global_risk_subscriber.handle_failed",
                    error=str(exc),
                    event_type=type(event).__name__,
                )

        _registered = True
        logger.info(
            "GlobalRiskSubscriber.registered",
            events=["IntelligenceEngineCompletedEvent"],
        )
