"""ProactiveWatchListener — domain logic for proactive alert scanning.

Owner: watchlist segment.

Responsibilities:
  - Subscribe to ProactiveWatchRequestedEvent (emitted by bot scheduler)
  - Load active alerts for the user
  - Run ScanService.scan_user() to detect triggered alerts
  - Call AlertService.process_triggered() to persist state transitions
  - Emit one ProactiveWatchAlertFiredEvent per fired alert

Does NOT touch Discord — that is ProactiveWatchSubscriber (bot segment).
Does NOT own scheduling — that is ProactiveWatchScheduler (bot segment).

Event chain:
    ProactiveWatchRequestedEvent  [bot → watchlist]
      → ProactiveWatchListener._handle()
        → ScanService.scan_user()             [watchlist]
        → AlertService.process_triggered()    [watchlist]
        → ProactiveWatchAlertFiredEvent × N   [watchlist → bot]
          → ProactiveWatchSubscriber           [bot]
            → Discord channel.send(embed)
"""

from __future__ import annotations

from src.platform.event_bus import get_event_bus
from src.platform.events import ProactiveWatchAlertFiredEvent, ProactiveWatchRequestedEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)


class ProactiveWatchListener:
    """Subscribe to ProactiveWatchRequestedEvent and run alert scan.

    Injected deps (all session-scoped internally — created per event):
        quote_service   — QuoteService singleton from bootstrap
        session_factory — AsyncSessionLocal
    """

    def __init__(self, quote_service, session_factory) -> None:
        self._quote_service = quote_service
        self._session_factory = session_factory

    def register(self) -> None:
        """Register handler on the event bus. Called once from bootstrap."""
        bus = get_event_bus()
        bus.subscribe(ProactiveWatchRequestedEvent, self._handle)
        logger.info("proactive_watch_listener.registered")

    async def _handle(self, event: ProactiveWatchRequestedEvent) -> None:
        """Run alert scan and emit ProactiveWatchAlertFiredEvent for each hit."""
        user_id = event.user_id
        if not user_id:
            logger.warning(
                "proactive_watch_listener.skipped",
                reason="user_id missing in event",
                event_id=event.event_id,
            )
            return

        logger.info(
            "proactive_watch_listener.started",
            user_id=user_id,
            phase=event.phase,
            event_id=event.event_id,
        )

        try:
            from src.thesis.ticker_direction_query import TickerDirectionQuery
            from src.watchlist.alert_service import AlertService
            from src.watchlist.scan_service import ScanService

            async with self._session_factory() as session:
                scan_svc = ScanService(
                    session=session,
                    quote_service=self._quote_service,
                    ticker_direction_query=TickerDirectionQuery(session),
                )
                result = await scan_svc.scan_user(user_id)

                if not result.triggered_alerts:
                    await session.commit()
                    logger.info(
                        "proactive_watch_listener.no_alerts",
                        user_id=user_id,
                        phase=event.phase,
                        signals_found=len(result.signals),
                    )
                    return

                alert_svc = AlertService(session)
                price_map: dict[str, float] = {
                    s.ticker: s.current_price
                    for s in result.signals
                    if hasattr(s, "current_price") and s.current_price is not None
                }
                fired = await alert_svc.process_triggered(
                    alerts=result.triggered_alerts,
                    price_map=price_map,
                )
                await session.commit()

            logger.info(
                "proactive_watch_listener.alerts_fired",
                user_id=user_id,
                phase=event.phase,
                fired_count=len(fired),
                tickers=sorted({a.ticker for a in fired}),
            )

            # Emit one event per fired alert so downstream can route/filter individually
            bus = get_event_bus()
            for alert in fired:
                await bus.publish(
                    ProactiveWatchAlertFiredEvent(
                        user_id=user_id,
                        alert_id=alert.id,
                        ticker=alert.ticker,
                        condition_type=alert.condition_type.value
                            if hasattr(alert.condition_type, "value")
                            else str(alert.condition_type),
                        threshold=alert.threshold or 0.0,
                        triggered_price=alert.triggered_price,
                        note=alert.note or "",
                        label=alert.label or "",
                        priority=alert.priority,
                        phase=event.phase,
                        scan_event_id=event.event_id,
                    )
                )

        except Exception as exc:
            logger.error(
                "proactive_watch_listener.error",
                user_id=user_id,
                phase=event.phase,
                error=str(exc),
            )
