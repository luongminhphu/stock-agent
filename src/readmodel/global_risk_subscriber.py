"""GlobalRiskSubscriber — listens to IntelligenceEngineCompletedEvent.

Owner: readmodel segment.

When IntelligenceEngine completes a run and emits
IntelligenceEngineCompletedEvent, this subscriber updates
GlobalRiskStore with the latest EngineVerdict so downstream
capabilities can read flagged tickers without DB queries.

Registration: platform/bootstrap.py (Commit 2).

Non-blocking: any error is logged and swallowed — never interrupts
the engine run or the event bus delivery chain.
"""

from __future__ import annotations

from src.platform.logging import get_logger
from src.readmodel.global_risk_store import get_global_risk_store

logger = get_logger(__name__)


class GlobalRiskSubscriber:
    """EventBus subscriber that keeps GlobalRiskStore in sync.

    Expected event type: IntelligenceEngineCompletedEvent
    Required fields on event:
      - user_id: str
      - verdict: EngineVerdict (any object with risk/flagged tickers)

    Gracefully handles missing fields so older event shapes don't crash.
    """

    async def handle(self, event: object) -> None:
        try:
            user_id: str = getattr(event, "user_id", None) or ""
            verdict = getattr(event, "verdict", None)

            if not user_id:
                logger.warning(
                    "global_risk_subscriber.missing_user_id",
                    event_type=type(event).__name__,
                )
                return

            if verdict is None:
                logger.warning(
                    "global_risk_subscriber.missing_verdict",
                    user_id=user_id,
                    event_type=type(event).__name__,
                )
                return

            store = get_global_risk_store()
            store.update(user_id, verdict)

            logger.debug(
                "global_risk_subscriber.handled",
                user_id=user_id,
                flagged_count=len(store.get_flagged_tickers(user_id)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "global_risk_subscriber.handle_failed",
                error=str(exc),
                event_type=type(event).__name__,
            )
