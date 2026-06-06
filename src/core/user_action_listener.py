"""UserActionFeedbackListener — closes the investor OS feedback loop.

Owner: core segment.

Purpose:
    When an investor explicitly acts (SELL, BUY, IGNORE_ALERT, MARK_REVIEWED,
    DEFER), this listener fans out the appropriate side-effects to:
      - thesis segment    (mark_closed)
      - watchlist segment (deprioritize, add, mute_alert, snooze)
      - memory / ai       (write UserBehaviorLog directly for pattern synthesis)
      - readmodel         (invalidate IntelligenceSnapshotStore hot cache — graceful
                           no-op until readmodel.intelligence_snapshot is implemented)

    core is the orchestrator. It DELEGATES to each segment — no domain
    logic lives here beyond routing.

Design rules:
    - Each side-effect opens its own session via get_session() and wraps the
      entire call in try/except. One failure must never block the others.
    - All calls are async fire-and-forget from the bus perspective.
    - Segment services are lazy-imported inside each adapter to avoid circular
      imports at module load time.
    - readmodel invalidation is a graceful no-op when the module does not yet
      exist (ImportError is caught and logged as a warning, not an error).

Session contract:
    get_session() from src.platform.db is an async context manager that
    commits on success and rolls back on exception. Each adapter call gets
    its own session — no cross-segment session sharing.

Boot::

    from src.core.user_action_listener import UserActionFeedbackListener
    UserActionFeedbackListener().register()
"""
from __future__ import annotations

from math import ceil

from src.platform.db import get_session
from src.platform.event_bus import EventBus, get_event_bus
from src.platform.events import UserActionEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Map UserActionEvent.action_type → UserBehaviorLog.signal
_ACTION_TO_SIGNAL: dict[str, str] = {
    "SELL": "sold",
    "BUY": "bought",
    "IGNORE_ALERT": "ignored",
    "MARK_REVIEWED": "watched",
    "DEFER": "ignored",
}


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

        # Always fan out to memory for pattern synthesis — regardless of action type
        await self._record_to_memory(event)

        # Evict hot cache — graceful no-op if readmodel module not yet present
        await self._invalidate_snapshot(event.user_id)

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    async def _on_sell(self, event: UserActionEvent) -> None:
        """SELL: close thesis lifecycle + deprioritize in watchlist."""
        await self._thesis_mark_closed(
            user_id=event.user_id,
            ticker=event.ticker,
            reason="closed",
        )
        await self._watchlist_deprioritize(
            user_id=event.user_id,
            ticker=event.ticker,
        )

    async def _on_buy(self, event: UserActionEvent) -> None:
        """BUY: ensure ticker is tracked in watchlist."""
        await self._watchlist_ensure_tracked(
            user_id=event.user_id,
            ticker=event.ticker,
            note=event.note or None,
        )

    async def _on_ignore_alert(self, event: UserActionEvent) -> None:
        """IGNORE_ALERT: mute specific alert + snooze ticker for mute_days."""
        if event.alert_id is not None:
            await self._watchlist_mute_alert(
                user_id=event.user_id,
                alert_id=event.alert_id,
                duration_days=event.mute_days,
            )
        # Also snooze the watchlist item itself for the same window
        await self._watchlist_snooze(
            user_id=event.user_id,
            ticker=event.ticker,
            duration_days=event.mute_days,
        )

    async def _on_mark_reviewed(self, event: UserActionEvent) -> None:
        """MARK_REVIEWED: record that investor reviewed this thesis (non-destructive).

        Calls ThesisService.touch_reviewed_at() to refresh updated_at so the
        readmodel knows when this thesis was last reviewed. Does NOT close,
        invalidate, or score the thesis.
        """
        logger.info(
            "user_action_listener.mark_reviewed",
            user_id=event.user_id,
            ticker=event.ticker,
            thesis_id=event.thesis_id,
            note=event.note or None,
        )
        if event.thesis_id is not None:
            await self._thesis_touch_reviewed(event.thesis_id, event.user_id)

    async def _on_defer(self, event: UserActionEvent) -> None:
        """DEFER: snooze watchlist item for snooze_hours (converted to days, min 1)."""
        duration_days = max(1, ceil(event.snooze_hours / 24))
        await self._watchlist_snooze(
            user_id=event.user_id,
            ticker=event.ticker,
            duration_days=duration_days,
        )

    # ------------------------------------------------------------------
    # Segment adapters
    # Each opens its own session. One failure never blocks the others.
    # ------------------------------------------------------------------

    async def _thesis_mark_closed(
        self,
        user_id: str,
        ticker: str,
        reason: str,
    ) -> None:
        try:
            from src.thesis.service import ThesisService

            async with get_session() as session:
                svc = ThesisService(session)
                result = await svc.mark_closed(
                    ticker=ticker,
                    user_id=user_id,
                    reason=reason,
                )
            if result is not None:
                logger.info(
                    "user_action_listener.thesis_closed",
                    ticker=ticker,
                    thesis_id=result.id,
                )
            else:
                logger.debug(
                    "user_action_listener.thesis_mark_closed.no_active_thesis",
                    ticker=ticker,
                )
        except ImportError:
            logger.warning(
                "user_action_listener.thesis_adapter_unavailable",
                hint="ThesisService not importable from src.thesis.service",
            )
        except Exception as exc:
            logger.error(
                "user_action_listener.thesis_close_failed",
                ticker=ticker,
                error=str(exc),
            )

    async def _thesis_touch_reviewed(
        self,
        thesis_id: int,
        user_id: str,
    ) -> None:
        """Call ThesisService.touch_reviewed_at() — non-destructive review stamp."""
        try:
            from src.thesis.service import ThesisService

            async with get_session() as session:
                svc = ThesisService(session)
                result = await svc.touch_reviewed_at(
                    thesis_id=thesis_id,
                    user_id=user_id,
                )
            if result is not None:
                logger.info(
                    "user_action_listener.thesis_reviewed_at_touched",
                    thesis_id=thesis_id,
                    user_id=user_id,
                )
            else:
                logger.debug(
                    "user_action_listener.thesis_touch_reviewed.not_found",
                    thesis_id=thesis_id,
                    user_id=user_id,
                )
        except ImportError:
            logger.warning(
                "user_action_listener.thesis_adapter_unavailable",
                hint="ThesisService not importable from src.thesis.service",
            )
        except Exception as exc:
            logger.error(
                "user_action_listener.thesis_touch_reviewed_failed",
                thesis_id=thesis_id,
                error=str(exc),
            )

    async def _watchlist_deprioritize(
        self,
        user_id: str,
        ticker: str,
    ) -> None:
        try:
            from src.watchlist.service import WatchlistService

            async with get_session() as session:
                svc = WatchlistService(session)
                await svc.deprioritize(user_id=user_id, ticker=ticker)
            logger.info(
                "user_action_listener.watchlist_deprioritized",
                user_id=user_id,
                ticker=ticker,
            )
        except ImportError:
            logger.warning(
                "user_action_listener.watchlist_adapter_unavailable",
                hint="WatchlistService not importable from src.watchlist.service",
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
        note: str | None,
    ) -> None:
        """Add ticker to watchlist if not already present (idempotent).

        Delegates to WatchlistService.ensure_tracked() which is idempotent:
        returns the existing item silently when ticker is already tracked,
        creates a new item otherwise. No exception swallowing needed.
        """
        try:
            from src.watchlist.service import WatchlistService

            async with get_session() as session:
                svc = WatchlistService(session)
                await svc.ensure_tracked(
                    user_id=user_id,
                    ticker=ticker,
                    note=note,
                )
                logger.info(
                    "user_action_listener.watchlist_ensure_tracked",
                    user_id=user_id,
                    ticker=ticker,
                )
        except ImportError:
            logger.warning(
                "user_action_listener.watchlist_adapter_unavailable",
                hint="WatchlistService not importable from src.watchlist.service",
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
        duration_days: int,
    ) -> None:
        try:
            from src.watchlist.service import WatchlistService

            async with get_session() as session:
                svc = WatchlistService(session)
                await svc.mute_alert(
                    alert_id=alert_id,
                    user_id=user_id,
                    duration_days=duration_days,
                )
            logger.info(
                "user_action_listener.alert_muted",
                alert_id=alert_id,
                user_id=user_id,
                duration_days=duration_days,
            )
        except ImportError:
            logger.warning(
                "user_action_listener.watchlist_adapter_unavailable",
                hint="WatchlistService not importable from src.watchlist.service",
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
        duration_days: int,
    ) -> None:
        try:
            from src.watchlist.service import WatchlistService

            async with get_session() as session:
                svc = WatchlistService(session)
                await svc.snooze(
                    user_id=user_id,
                    ticker=ticker,
                    duration_days=duration_days,
                )
            logger.info(
                "user_action_listener.watchlist_snoozed",
                user_id=user_id,
                ticker=ticker,
                duration_days=duration_days,
            )
        except ImportError:
            logger.warning(
                "user_action_listener.watchlist_adapter_unavailable",
                hint="WatchlistService not importable from src.watchlist.service",
            )
        except Exception as exc:
            logger.error(
                "user_action_listener.watchlist_snooze_failed",
                ticker=ticker,
                error=str(exc),
            )

    async def _record_to_memory(self, event: UserActionEvent) -> None:
        """Write a UserBehaviorLog row directly — no consolidator needed.

        MemoryConsolidator is a batch AI distillation service; it should not
        be called on every user action. UserBehaviorLog is the canonical write
        path for explicit investor signals, and writing it directly is:
          - simpler (no AIClient dependency)
          - safer (one INSERT, no AI call latency)
          - auditable (each action → one row, exact timestamp)

        Pattern synthesis (MemoryConsolidator.synthesize_patterns) reads
        UserBehaviorLog as its source-of-truth — this write path feeds that
        read path correctly without any intermediary.

        Signal mapping (action_type → UserBehaviorLog.signal):
          SELL          → sold
          BUY           → bought
          IGNORE_ALERT  → ignored
          MARK_REVIEWED → watched
          DEFER         → ignored   (temporarily deprioritised)
          (anything else) → action_type.lower()

        Note on interaction_log_id:
          UserActionEvent.verdict_id is NOT a FK to AIInteractionLog.id —
          it is a thesis verdict reference. We set interaction_log_id=None
          to avoid a phantom FK. If a genuine AIInteractionLog link is needed
          in future, wire it explicitly via a new event field.
        """
        try:
            from src.ai.memory.user_behavior_log import UserBehaviorLog

            signal = _ACTION_TO_SIGNAL.get(
                event.action_type, event.action_type.lower()
            )

            async with get_session() as session:
                session.add(
                    UserBehaviorLog(
                        user_id=event.user_id,
                        signal=signal,
                        source="feedback_listener",
                        interaction_log_id=None,  # verdict_id ≠ AIInteractionLog FK
                        ticker=event.ticker.upper() if event.ticker else None,
                        agent_type="feedback_loop",
                        note=(event.note[:512] if event.note else None),
                    )
                )
            logger.info(
                "user_action_listener.memory_recorded",
                action_type=event.action_type,
                signal=signal,
                ticker=event.ticker,
            )
        except ImportError:
            logger.warning(
                "user_action_listener.memory_adapter_unavailable",
                hint="UserBehaviorLog not importable from src.ai.memory.user_behavior_log",
            )
        except Exception as exc:
            logger.error(
                "user_action_listener.memory_record_failed",
                ticker=event.ticker,
                error=str(exc),
            )

    async def _invalidate_snapshot(self, user_id: str) -> None:
        """Evict hot cache so next query triggers a fresh engine cycle.

        Calls IntelligenceSnapshotStore.invalidate(user_id) — removes the hot
        TTL layer so the next read falls back to the warm layer (stale=True)
        and triggers a background refresh in the API/bot layer.
        """
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
