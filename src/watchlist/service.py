"""Watchlist service — CRUD for watchlist items and alerts.

Owner: watchlist segment.
Bot commands and API routes use this; they do not import models directly.

DTOs and Exceptions → dtos.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.watchlist.alert_service import AlertNotFoundError
from src.watchlist.dtos import (
    AddToWatchlistInput,
    CreateAlertInput,
    WatchlistItemAlreadyExistsError,
    WatchlistItemNotFoundError,
)
from src.watchlist.models import (
    Alert,
    AlertConditionType,
    AlertStatus,
    WatchlistItem,
)
from src.watchlist.repository import SignalEventRepository, WatchlistRepository

AddAlertInput = CreateAlertInput

logger = get_logger(__name__)

# Re-export để backward compat với code import từ service.py
__all__ = [
    "WatchlistService",
    "WatchlistItemWithPrice",
    "AddToWatchlistInput",
    "CreateAlertInput",
    "AddAlertInput",
    "WatchlistItemNotFoundError",
    "WatchlistItemAlreadyExistsError",
    "AlertNotFoundError",
]

# Penalty applied to WatchlistItem.priority when investor ignores an alert.
# Higher priority value = lower display rank (priority=100 is default/bottom).
_IGNORE_PRIORITY_PENALTY = 20
_PRIORITY_MAX = 999


@dataclass
class WatchlistItemWithPrice:
    """Watchlist item enriched with live price data.

    Returned by WatchlistService.list_items_with_prices().
    price_str is pre-formatted for display (e.g. "12,345 (🔺+1.2%)").
    change_pct is None when the quote fetch failed for this ticker.
    """

    ticker: str
    note: str | None
    price_str: str
    change_pct: float | None


class WatchlistService:
    """Manage watchlist items, alerts, and signal event lifecycle."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = WatchlistRepository(session)
        self._signal_repo = SignalEventRepository(session)
        self._session = session

    async def add(self, inp: AddToWatchlistInput) -> WatchlistItem:
        existing = await self._repo.get_item(inp.user_id, inp.ticker)
        if existing:
            raise WatchlistItemAlreadyExistsError(
                f"{inp.ticker} is already in watchlist for user {inp.user_id}"
            )
        item = WatchlistItem(
            user_id=inp.user_id,
            ticker=inp.ticker.upper(),
            note=inp.note or None,
            thesis_id=inp.thesis_id,
            priority=inp.priority,
        )
        await self._repo.save_item(item)
        logger.info("watchlist.added", user_id=inp.user_id, ticker=inp.ticker)
        return item

    async def remove(self, user_id: str, ticker: str) -> None:
        item = await self._repo.get_item(user_id, ticker)
        if item is None:
            raise WatchlistItemNotFoundError(
                f"{ticker} not found in watchlist for user {user_id}"
            )
        await self._repo.delete_item(item)
        logger.info("watchlist.removed", user_id=user_id, ticker=ticker)

    async def list_items(self, user_id: str) -> list[WatchlistItem]:
        return await self._repo.list_for_user(user_id)

    async def list_items_with_prices(
        self,
        user_id: str,
        quote_service: object,
    ) -> list[WatchlistItemWithPrice]:
        """Return watchlist items enriched with live price data.

        Performs a single bulk quote fetch. Individual tickers missing from
        the bulk result get price_str='N/A' and change_pct=None — same
        graceful-degrade behaviour as the previous bot-side implementation.

        Args:
            user_id:       Owner of the watchlist.
            quote_service: QuoteService instance (duck-typed; injected by caller).

        Returns:
            List of WatchlistItemWithPrice in watchlist order.
        """
        items = await self._repo.list_for_user(user_id)
        if not items:
            return []

        tickers = [i.ticker for i in items]
        try:
            quotes = await quote_service.get_bulk_quotes(tickers)  # type: ignore[union-attr]
            price_map = {q.ticker: q for q in quotes}
        except Exception as exc:
            logger.warning(
                "watchlist.list_with_prices.bulk_fetch_failed",
                user_id=user_id,
                error=str(exc),
            )
            price_map: dict[str, object] = {}

        result: list[WatchlistItemWithPrice] = []
        for item in items:
            q = price_map.get(item.ticker)
            if q is not None:
                change_pct: float = getattr(q, "change_pct", 0.0)
                icon = "🔺" if change_pct >= 0 else "🔻"
                price_str = f"{getattr(q, 'price', 0):,.0f} ({icon}{change_pct:+.1f}%)"
            else:
                price_str = "N/A"
                change_pct = None  # type: ignore[assignment]
            result.append(
                WatchlistItemWithPrice(
                    ticker=item.ticker,
                    note=item.note,
                    price_str=price_str,
                    change_pct=change_pct,
                )
            )
        return result

    async def get_tickers(self, user_id: str) -> list[str]:
        items = await self._repo.list_for_user(user_id)
        return [i.ticker for i in items]

    async def update_note(self, user_id: str, ticker: str, note: str) -> WatchlistItem:
        item = await self._repo.get_item(user_id, ticker)
        if item is None:
            raise WatchlistItemNotFoundError(f"{ticker} not in watchlist")
        item.note = note
        await self._repo.save_item(item)
        return item

    async def update_priority(self, user_id: str, ticker: str, priority: int) -> WatchlistItem:
        """Update the priority of a watchlist item.

        Args:
            user_id: Owner of the watchlist.
            ticker:  Stock symbol (case-insensitive).
            priority: New priority value (lower = higher priority).

        Returns:
            Updated WatchlistItem.

        Raises:
            WatchlistItemNotFoundError: If ticker is not in the user's watchlist.
        """
        item = await self._repo.get_item(user_id, ticker.upper())
        if item is None:
            raise WatchlistItemNotFoundError(f"{ticker} not in watchlist for user {user_id}")
        item.priority = priority
        await self._repo.save_item(item)
        logger.info("watchlist.priority_updated", user_id=user_id, ticker=ticker, priority=priority)
        return item

    async def create_alert(self, inp: CreateAlertInput) -> Alert:
        if isinstance(inp.condition_type, str):
            inp.condition_type = AlertConditionType(inp.condition_type.lower())
        if inp.watchlist_item_id is None:
            item = await self._repo.get_item(inp.user_id, inp.ticker)
            if item is None:
                raise WatchlistItemNotFoundError(
                    f"{inp.ticker} not found in watchlist for user {inp.user_id}"
                )
            inp.watchlist_item_id = item.id

        alert = Alert(
            user_id=inp.user_id,
            ticker=inp.ticker.upper(),
            condition_type=inp.condition_type,
            threshold=inp.threshold,
            note=inp.note or None,
            watchlist_item_id=inp.watchlist_item_id,
            status=AlertStatus.ACTIVE,
        )
        await self._repo.save_alert(alert)
        logger.info(
            "alert.created",
            user_id=inp.user_id,
            ticker=inp.ticker,
            condition=inp.condition_type,
            threshold=inp.threshold,
        )
        return alert

    async def dismiss_alert(self, alert_id: int, user_id: str) -> None:
        alerts = await self._repo.list_active_alerts(user_id)
        alert = next((a for a in alerts if a.id == alert_id), None)
        if alert is None:
            raise AlertNotFoundError(f"Alert {alert_id} not found")
        alert.status = AlertStatus.DISMISSED
        await self._repo.save_alert(alert)
        logger.info("alert.dismissed", alert_id=alert_id)

    async def list_active_alerts(self, user_id: str) -> list[Alert]:
        return await self._repo.list_active_alerts(user_id)

    # ------------------------------------------------------------------
    # Feedback-loop helpers (called by core/feedback_listener.py)
    # ------------------------------------------------------------------

    async def deprioritize(
        self,
        user_id: str,
        ticker: str,
        *,
        penalty: int = _IGNORE_PRIORITY_PENALTY,
    ) -> WatchlistItem | None:
        """Lower the display rank of *ticker* after investor ignores/exits.

        Increments WatchlistItem.priority by *penalty* (higher int = lower rank).
        Capped at _PRIORITY_MAX to prevent runaway values.
        No-op (returns None) when the ticker is not in the watchlist.

        Called by:
          core.FeedbackListener.on_user_action() for SELL and IGNORE_ALERT events.

        Args:
            user_id: Owner of the watchlist.
            ticker:  Stock symbol (case-insensitive).
            penalty: Priority delta to add (default 20).

        Returns:
            Updated WatchlistItem, or None if not found.
        """
        item = await self._repo.get_item(user_id, ticker.upper())
        if item is None:
            logger.debug(
                "watchlist.deprioritize.not_found",
                user_id=user_id,
                ticker=ticker,
            )
            return None
        item.priority = min(item.priority + penalty, _PRIORITY_MAX)
        await self._repo.save_item(item)
        logger.info(
            "watchlist.deprioritized",
            user_id=user_id,
            ticker=ticker,
            new_priority=item.priority,
        )
        return item

    async def mute_alert(
        self,
        alert_id: int,
        user_id: str,
        *,
        duration_days: int = 7,
    ) -> Alert | None:
        """Mute an alert for *duration_days* by bumping its effective_cooldown_hours.

        Uses Alert.effective_cooldown_hours as a snooze duration proxy.
        The existing AlertService.reactivate_cooled_down() already reads
        effective_cooldown_hours — so this integrates with the current
        cooldown logic without a schema migration.

        NOTE: When the Alert model gains a proper `snoozed_until` DateTime
        column (follow-up migration), replace the cooldown_hours approach
        with a direct datetime comparison.

        Called by:
          core.FeedbackListener.on_user_action() for IGNORE_ALERT events.

        Args:
            alert_id:      ID of the alert to mute.
            user_id:       Owner (for ownership check).
            duration_days: How many days to suppress the alert (default 7).

        Returns:
            Updated Alert, or None if not found / not owned.
        """
        return await self._snooze_alert_internal(
            alert_id=alert_id,
            user_id=user_id,
            hours=duration_days * 24,
        )

    async def snooze_alert(
        self,
        alert_id: int,
        user_id: str,
        *,
        until: datetime | None = None,
        hours: int | None = None,
    ) -> Alert | None:
        """Snooze an alert until a specific datetime or for N hours.

        Exactly one of *until* or *hours* must be supplied.

        Uses Alert.effective_cooldown_hours as snooze storage (same approach
        as mute_alert). When Alert gains a `snoozed_until` DateTime column,
        this method should be updated to persist the exact datetime.

        Called by:
          bot commands: /snooze <alert_id> [until <date>]
          core.FeedbackListener (future — when investor provides specific date)

        Args:
            alert_id: ID of the alert.
            user_id:  Owner.
            until:    Snooze until this UTC datetime.
            hours:    Snooze for this many hours from now.

        Returns:
            Updated Alert, or None if not found / not owned.

        Raises:
            ValueError: If neither or both of *until*/*hours* are supplied.
        """
        if (until is None) == (hours is None):
            raise ValueError("Provide exactly one of 'until' or 'hours'.")

        if hours is None:
            assert until is not None
            now = datetime.now(UTC)
            snooze_until = until if until.tzinfo else until.replace(tzinfo=UTC)
            hours = max(1, int((snooze_until - now).total_seconds() // 3600))

        return await self._snooze_alert_internal(
            alert_id=alert_id,
            user_id=user_id,
            hours=hours,
        )

    async def _snooze_alert_internal(
        self,
        alert_id: int,
        user_id: str,
        hours: int,
    ) -> Alert | None:
        """Internal: set effective_cooldown_hours and DISMISS the alert.

        Dismissing transitions the alert out of ACTIVE so it won't fire
        during the snooze window. The existing reactivate_cooled_down()
        scheduler will restore it to ACTIVE after the cooldown expires.
        """
        # Load alert — search across all statuses (not just ACTIVE) so
        # we can snooze a TRIGGERED alert that hasn't been dismissed yet.
        stmt = select(Alert).where(
            Alert.id == alert_id,
            Alert.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        alert = result.scalar_one_or_none()

        if alert is None:
            logger.warning(
                "watchlist.snooze.not_found",
                alert_id=alert_id,
                user_id=user_id,
            )
            return None

        alert.effective_cooldown_hours = max(1, hours)
        alert.status = AlertStatus.DISMISSED  # reactivate_cooled_down() will restore
        await self._repo.save_alert(alert)
        logger.info(
            "watchlist.alert_snoozed",
            alert_id=alert_id,
            user_id=user_id,
            hours=hours,
        )
        return alert

    # ------------------------------------------------------------------
    # Signal event lifecycle (called by ai segment via public API only)
    # ------------------------------------------------------------------

    async def mark_signal_processed(self, event_id: str) -> bool:
        """Stamp processed_at = now(UTC) on the SignalEvent row for event_id.

        Lookup is by SignalEvent.event_id (UUID string) which is unique per row.
        Caller is responsible for commit after this call.

        Returns:
            True  — row found and stamped.
            False — row not found (already processed or never existed).

        Consumed exclusively by ai.ProactiveAlertAgent._mark_processed()
        to avoid ai segment importing watchlist repo/model layer directly.
        """
        from src.watchlist.models import SignalEvent

        stmt = select(SignalEvent).where(SignalEvent.event_id == event_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self._signal_repo.mark_processed(row)
        return True
