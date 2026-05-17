"""CacheSubscriber — readmodel cache invalidation via event bus.

Owner: readmodel segment.

Responsibility:
    Listen to domain events emitted by other segments and invalidate the
    module-level DashboardTTLCache in dashboard_service.py so that the next
    API poll picks up fresh data instead of a stale snapshot.

Wired events:
    WatchlistScanCompletedEvent
        → invalidate "scan_latest" for the event's user_id.
          Falls back to invalidate_all("scan_latest") if user_id is empty
          (legacy emitters that haven't been updated to Wave 3 yet).

    BriefingReadyEvent
        → invalidate "brief_latest" for the event's user_id.
          Same fallback behaviour as above.

Usage (app startup, e.g. src/platform/startup.py or lifespan hook)::

    from src.readmodel import CacheSubscriber
    CacheSubscriber.register()          # idempotent — safe to call multiple times

Direct cache busting (from a write path without going through the bus)::

    from src.readmodel import get_readmodel_cache
    get_readmodel_cache().invalidate("scan_latest", user_id)

Design notes:
- Handlers are thin: log + call cache primitive. No business logic.
- import from dashboard_service._cache is intentional — single source of truth
  for the shared cache instance.
- register() is idempotent via a module-level guard flag so double-calling
  at startup (e.g. during test setup) doesn't register duplicate handlers.
"""
from __future__ import annotations

from src.platform.event_bus import get_event_bus
from src.platform.events import BriefingReadyEvent, WatchlistScanCompletedEvent
from src.platform.logging import get_logger
from src.readmodel.cache import DashboardTTLCache
from src.readmodel import dashboard_service as _ds

logger = get_logger(__name__)

_registered = False


def get_cache() -> DashboardTTLCache:
    """Return the shared DashboardTTLCache instance used by DashboardService.

    External callers (write paths in watchlist, briefing, thesis segments) can
    use this to bust specific cache entries immediately after a write without
    going through the event bus.

    Example::

        from src.readmodel.cache_subscriber import get_cache
        get_cache().invalidate("scan_latest", user_id)
    """
    return _ds._cache


class CacheSubscriber:
    """Registers event handlers on the global EventBus to invalidate readmodel cache."""

    @staticmethod
    def register() -> None:
        """Wire cache-invalidation handlers onto the global EventBus.

        Idempotent — safe to call multiple times (e.g. in tests).
        Only the first call registers; subsequent calls are no-ops.
        """
        global _registered
        if _registered:
            logger.debug("CacheSubscriber.register() skipped — already registered")
            return

        bus = get_event_bus()

        @bus.subscribe(WatchlistScanCompletedEvent)
        async def _on_scan_completed(event: WatchlistScanCompletedEvent) -> None:
            cache = get_cache()
            if event.user_id:
                cache.invalidate("scan_latest", event.user_id)
                logger.debug(
                    "cache.invalidated",
                    namespace="scan_latest",
                    user_id=event.user_id,
                    trigger="WatchlistScanCompletedEvent",
                )
            else:
                count = cache.invalidate_all("scan_latest")
                logger.debug(
                    "cache.invalidated_all",
                    namespace="scan_latest",
                    count=count,
                    trigger="WatchlistScanCompletedEvent",
                    reason="no user_id in event",
                )

        @bus.subscribe(BriefingReadyEvent)
        async def _on_briefing_ready(event: BriefingReadyEvent) -> None:
            cache = get_cache()
            if event.user_id:
                cache.invalidate("brief_latest", event.user_id)
                logger.debug(
                    "cache.invalidated",
                    namespace="brief_latest",
                    user_id=event.user_id,
                    trigger="BriefingReadyEvent",
                )
            else:
                count = cache.invalidate_all("brief_latest")
                logger.debug(
                    "cache.invalidated_all",
                    namespace="brief_latest",
                    count=count,
                    trigger="BriefingReadyEvent",
                    reason="no user_id in event",
                )

        _registered = True
        logger.info(
            "CacheSubscriber.registered",
            events=["WatchlistScanCompletedEvent", "BriefingReadyEvent"],
        )
