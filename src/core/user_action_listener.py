"""UserActionFeedbackListener — closes the investor OS feedback loop.

Owner: core segment.

Purpose:
    When an investor explicitly acts (SELL, BUY, IGNORE_ALERT, MARK_REVIEWED,
    DEFER), this listener fans out the appropriate side-effects to:
      - thesis segment   (mark_closed, touch_reviewed_at)
      - watchlist segment (deprioritize, ensure_tracked, mute_alert, snooze)
      - memory / ai      (record_action for pattern synthesis)
      - readmodel        (invalidate IntelligenceSnapshotStore hot cache)

    core is the orchestrator. It DELEGATES to each segment — no domain
    logic lives here beyond routing.

Design rules:
    - Each side-effect is wrapped in its own try/except.
      One failure must never block the others.
    - All calls are async fire-and-forget from the bus perspective.
    - Adapters (thesis_adapter, watchlist_adapter, memory_adapter) are
      lazy-imported to avoid circular imports at module load time.
    - readmodel invalidation is direct (no adapter needed — readmodel
      is a read concern, safe to call from core).

Boot::

    from src.core.user_action_listener import UserActionFeedbackListener
    UserActionFeedbackListener().register()
"""
from __future__ import annotations

from src.platform.event_bus import EventBus, get_event_bus
from src.platform.events import UserActionEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)


class UserActionFeedbackListener:
    """Subscribe to UserActionEvent → dispatch segment side-effects."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self._bus = bus or get_event_bus()

    def register(self) -> None:
        self._bus.subscribe(UserActionEvent, self._handle)
        logger.info("user_action_listener.registered")

    # ------------------------------------------------------------------
    # Main handler
    # ------------------------------------------------------------------

    async def _handle(self, event: UserActionEvent) -> None:
        logger.info(
            "user_action_listener.received",
            user_id=event.user_id,
            action_type=event.action_type,
            ticker=event.ticker,
            thesis_id=event.thesis_id,
            alert_id=event.alert_id,
        )

        action = event.action_type

        if action == "SELL":
            await self._on_sell(event)
        elif action == "BUY":
            await self._on_buy(event)
        elif action == "IGNORE_ALERT":
            await self._on_ignore_alert(event)
        elif action == "MARK_REVIEWED":
            await self._on_mark_reviewed(event)
        elif action == "DEFER":
            await self._on_defer(event)
        else:
            logger.warning("user_action_listener.unknown_action", action_type=action)
            return

        # Always record to memory for pattern synthesis — regardless of action type
        await self._record_to_memory(event)

        # Invalidate hot cache so next bot/api query triggers a fresh engine cycle
        await self._invalidate_snapshot(event.user_id)

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    async def _on_sell(self, event: UserActionEvent) -> None:
        """SELL: close thesis lifecycle + deprioritize in watchlist."""
        if event.thesis_id is not None:
            await self._thesis_mark_closed(
                thesis_id=event.thesis_id,
                user_id=event.user_id,
                ticker=event.ticker,
                close_reason="user_sold",
                outcome_price=event.price,
                note=event.note,
            )

        await self._watchlist_deprioritize(
            user_id=event.user_id,
            ticker=event.ticker,
            reason="sold",
        )

    async def _on_buy(self, event: UserActionEvent) -> None:
        """BUY: ensure ticker is tracked in watchlist."""
        await self._watchlist_ensure_tracked(
            user_id=event.user_id,
            ticker=event.ticker,
            note=event.note,
        )

    async def _on_ignore_alert(self, event: UserActionEvent) -> None:
        """IGNORE_ALERT: mute alert for N days."""
        if event.alert_id is not None:
            await self._watchlist_mute_alert(
                user_id=event.user_id,
                alert_id=event.alert_id,
                ticker=event.ticker,
                mute_days=event.mute_days,
            )

    async def _on_mark_reviewed(self, event: UserActionEvent) -> None:
        """MARK_REVIEWED: touch thesis reviewed_at timestamp."""
        if event.thesis_id is not None:
            await self._thesis_touch_reviewed(
                thesis_id=event.thesis_id,
                user_id=event.user_id,
                ticker=event.ticker,
                note=event.note,
            )

    async def _on_defer(self, event: UserActionEvent) -> None:
        """DEFER: snooze watchlist for N hours."""
        await self._watchlist_snooze(
            user_id=event.user_id,
            ticker=event.ticker,
            snooze_hours=event.snooze_hours,
        )

    # ------------------------------------------------------------------
    # Segment adapters — lazy-imported, each wrapped in try/except
    # ------------------------------------------------------------------

    async def _thesis_mark_closed(
        self,
        thesis_id: int,
        user_id: str,
        ticker: str,
        close_reason: str,
        outcome_price: float | None,
        note: str,
    ) -> None:
        try:
            from src.thesis.service import ThesisService  # type: ignore[import]

            await ThesisService.mark_closed(
                thesis_id=thesis_id,
                close_reason=close_reason,
                outcome_price=outcome_price,
                note=note or None,
            )
            logger.info(
                "user_action_listener.thesis_closed",
                thesis_id=thesis_id,
                ticker=ticker,
            )
        except ImportError:
            logger.warning(
                "user_action_listener.thesis_adapter_unavailable",
                hint="ThesisService.mark_closed not found — implement in thesis.service",
            )
        except Exception as exc:
            logger.error(
                "user_action_listener.thesis_close_failed",
                thesis_id=thesis_id,
                error=str(exc),
            )

    async def _thesis_touch_reviewed(
        self,
        thesis_id: int,
        user_id: str,
        ticker: str,
        note: str,
    ) -> None:
        try:
            from src.thesis.service import ThesisService  # type: ignore[import]

            await ThesisService.touch_reviewed_at(
                thesis_id=thesis_id,
                note=note or None,
            )
            logger.info(
                "user_action_listener.thesis_reviewed",
                thesis_id=thesis_id,
                ticker=ticker,
            )
        except ImportError:
            logger.warning(
                "user_action_listener.thesis_adapter_unavailable",
                hint="ThesisService.touch_reviewed_at not found — implement in thesis.service",
            )
        except Exception as exc:
            logger.error(
                "user_action_listener.thesis_review_failed",
                thesis_id=thesis_id,
                error=str(exc),
            )

    async def _watchlist_deprioritize(
        self,
        user_id: str,
        ticker: str,
        reason: str,
    ) -> None:
        try:
            from src.watchlist.service import WatchlistService  # type: ignore[import]

            await WatchlistService.deprioritize(user_id=user_id, ticker=ticker, reason=reason)
            logger.info(
                "user_action_listener.watchlist_deprioritized",
                ticker=ticker,
                reason=reason,
            )
        except ImportError:
            logger.warning(
                "user_action_listener.watchlist_adapter_unavailable",
                hint="WatchlistService.deprioritize not found — implement in watchlist.service",
            )
        except Exception as exc:
            logger.error(
                "user_action_listener.watchlist_deprioritize_failed",
                ticker=ticker,
                error=str(exc),
            )

    async def _watchlist_ensure_tracked(
        self,
        user_id: str,
        ticker: str,
        note: str,
    ) -> None:
        try:
            from src.watchlist.service import WatchlistService  # type: ignore[import]

            await WatchlistService.ensure_tracked(user_id=user_id, ticker=ticker, note=note or None)
            logger.info(
                "user_action_listener.watchlist_tracked",
                ticker=ticker,
            )
        except ImportError:
            logger.warning(
                "user_action_listener.watchlist_adapter_unavailable",
                hint="WatchlistService.ensure_tracked not found — implement in watchlist.service",
            )
        except Exception as exc:
            logger.error(
                "user_action_listener.watchlist_track_failed",
                ticker=ticker,
                error=str(exc),
            )

    async def _watchlist_mute_alert(
        self,
        user_id: str,
        alert_id: int,
        ticker: str,
        mute_days: int,
    ) -> None:
        try:
            from src.watchlist.service import WatchlistService  # type: ignore[import]

            await WatchlistService.mute_alert(
                user_id=user_id,
                alert_id=alert_id,
                mute_days=mute_days,
            )
            logger.info(
                "user_action_listener.alert_muted",
                alert_id=alert_id,
                ticker=ticker,
                mute_days=mute_days,
            )
        except ImportError:
            logger.warning(
                "user_action_listener.watchlist_adapter_unavailable",
                hint="WatchlistService.mute_alert not found — implement in watchlist.service",
            )
        except Exception as exc:
            logger.error(
                "user_action_listener.mute_alert_failed",
                alert_id=alert_id,
                error=str(exc),
            )

    async def _watchlist_snooze(
        self,
        user_id: str,
        ticker: str,
        snooze_hours: int,
    ) -> None:
        try:
            from src.watchlist.service import WatchlistService  # type: ignore[import]

            await WatchlistService.snooze(
                user_id=user_id,
                ticker=ticker,
                snooze_hours=snooze_hours,
            )
            logger.info(
                "user_action_listener.watchlist_snoozed",
                ticker=ticker,
                snooze_hours=snooze_hours,
            )
        except ImportError:
            logger.warning(
                "user_action_listener.watchlist_adapter_unavailable",
                hint="WatchlistService.snooze not found — implement in watchlist.service",
            )
        except Exception as exc:
            logger.error(
                "user_action_listener.watchlist_snooze_failed",
                ticker=ticker,
                error=str(exc),
            )

    async def _record_to_memory(
        self,
        event: UserActionEvent,
    ) -> None:
        """Fan out action to AI memory for pattern synthesis."""
        try:
            from src.ai.memory.consolidator import MemoryConsolidator  # type: ignore[import]

            await MemoryConsolidator.record_user_action(
                user_id=event.user_id,
                action_type=event.action_type,
                ticker=event.ticker,
                thesis_id=event.thesis_id,
                verdict_id=event.verdict_id or None,
                note=event.note or None,
                price=event.price,
            )
            logger.info(
                "user_action_listener.memory_recorded",
                action_type=event.action_type,
                ticker=event.ticker,
            )
        except ImportError:
            logger.warning(
                "user_action_listener.memory_adapter_unavailable",
                hint="MemoryConsolidator.record_user_action not found — add to ai.memory.consolidator",
            )
        except Exception as exc:
            logger.error(
                "user_action_listener.memory_record_failed",
                ticker=event.ticker,
                error=str(exc),
            )

    async def _invalidate_snapshot(
        self,
        user_id: str,
    ) -> None:
        """Evict hot cache so next query triggers a fresh engine cycle."""
        try:
            from src.readmodel.intelligence_snapshot import get_intelligence_snapshot

            get_intelligence_snapshot().invalidate(user_id)
            logger.info(
                "user_action_listener.snapshot_invalidated",
                user_id=user_id,
            )
        except Exception as exc:
            logger.error(
                "user_action_listener.snapshot_invalidate_failed",
                user_id=user_id,
                error=str(exc),
            )
