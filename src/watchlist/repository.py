"""Watchlist repository — async DB access for watchlist segment.

Owner: watchlist segment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.watchlist.models import Alert, AlertStatus, Reminder, WatchlistItem, WatchlistScan

if TYPE_CHECKING:
    from src.watchlist.models import SignalEvent


class WatchlistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # WatchlistItem
    # ------------------------------------------------------------------

    async def get_item(self, user_id: str, ticker: str) -> WatchlistItem | None:
        stmt = (
            select(WatchlistItem)
            .where(WatchlistItem.user_id == user_id)
            .where(WatchlistItem.ticker == ticker.upper())
            .options(selectinload(WatchlistItem.alerts))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: str) -> list[WatchlistItem]:
        stmt = (
            select(WatchlistItem)
            .where(WatchlistItem.user_id == user_id)
            .options(selectinload(WatchlistItem.alerts))
            .order_by(WatchlistItem.priority.asc(), WatchlistItem.added_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all_tickers(self) -> list[str]:
        """Return distinct tickers across all users — used by ScanService."""
        stmt = select(WatchlistItem.ticker).distinct()
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def save_item(self, item: WatchlistItem) -> WatchlistItem:
        self._session.add(item)
        await self._session.flush()
        return item

    async def delete_item(self, item: WatchlistItem) -> None:
        await self._session.delete(item)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Alert
    # ------------------------------------------------------------------

    async def list_active_alerts(self, user_id: str) -> list[Alert]:
        stmt = (
            select(Alert)
            .where(Alert.user_id == user_id)
            .where(Alert.status == AlertStatus.ACTIVE)
            .order_by(Alert.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def save_alert(self, alert: Alert) -> Alert:
        self._session.add(alert)
        await self._session.flush()
        return alert

    # ------------------------------------------------------------------
    # Reminder
    # ------------------------------------------------------------------

    async def get_reminder(self, watchlist_item_id: int) -> Reminder | None:
        """Return the Reminder for a WatchlistItem, or None if not set."""
        stmt = select(Reminder).where(Reminder.watchlist_item_id == watchlist_item_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def save_reminder(self, reminder: Reminder) -> Reminder:
        """Persist (insert or update) a Reminder and flush."""
        self._session.add(reminder)
        await self._session.flush()
        return reminder

    async def list_due_reminders(self, before: datetime) -> list[Reminder]:
        """Return all enabled Reminders whose next send time is <= before.

        A reminder is considered due when:
          - enabled = True
          - last_sent_at IS NULL  (never sent), OR
          - last_sent_at + frequency_delta <= before

        The frequency_delta check is done in Python by ReminderService
        using Reminder.is_due(); this query returns candidates with
        last_sent_at IS NULL OR last_sent_at < before as a coarse filter.
        """
        stmt = (
            select(Reminder)
            .where(Reminder.enabled.is_(True))
            .where(
                (Reminder.last_sent_at.is_(None)) | (Reminder.last_sent_at < before)
            )
            .options(selectinload(Reminder.watchlist_item))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # WatchlistScan
    # ------------------------------------------------------------------

    async def get_latest_scan(self, user_id: str) -> WatchlistScan | None:
        stmt = (
            select(WatchlistScan)
            .where(WatchlistScan.user_id == user_id)
            .order_by(WatchlistScan.scanned_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


# ── Signal Events ──────────────────────────────────────────────────────────────────


class SignalEventRepository:
    """Persist and query SignalEvent rows (signal_events table).

    Owner: watchlist segment.
    Called only by ScanService._emit_events() — no other writer.
    Reader: ProactiveAlertAgent (ai segment) via list_pending / mark_processed.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, event: "SignalEvent") -> "SignalEvent":
        """Stage a new SignalEvent row and flush (no commit — caller owns tx)."""
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_pending(self, limit: int = 100) -> list["SignalEvent"]:
        """Return unprocessed signal events ordered oldest-first.

        An event is 'pending' when processed_at IS NULL.
        Used by ProactiveAlertAgent to drain the inbox.
        """
        from src.watchlist.models import SignalEvent  # local import avoids circular at module level

        stmt = (
            select(SignalEvent)
            .where(SignalEvent.processed_at.is_(None))
            .order_by(SignalEvent.occurred_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_processed(self, event: "SignalEvent") -> None:
        """Stamp processed_at = now(UTC) and flush."""
        event.processed_at = datetime.now(UTC)
        await self._session.flush()

    async def list_for_symbol(
        self,
        symbol: str,
        user_id: str | None = None,
        limit: int = 50,
    ) -> list["SignalEvent"]:
        """Return recent signal events for a ticker, newest-first.

        Args:
            symbol:  Ticker (case-insensitive — normalised to upper).
            user_id: Optional filter by user.
            limit:   Max rows returned.
        """
        from src.watchlist.models import SignalEvent

        stmt = (
            select(SignalEvent)
            .where(SignalEvent.ticker == symbol.upper())
            .order_by(SignalEvent.occurred_at.desc())
            .limit(limit)
        )
        if user_id:
            stmt = stmt.where(SignalEvent.user_id == user_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def has_recent_signal(
        self,
        ticker: str,
        signal_type: str,
        hours: int = 24,
        user_id: str | None = None,
    ) -> bool:
        """True nếu có signal_type cho ticker trong `hours` giờ gần nhất.

        Dùng bởi ScanService để enrich prior_risk_spike trước khi gọi SignalEngine.
        SELECT 1 + LIMIT 1 — không fetch full row, minimal I/O.

        Args:
            ticker:      Stock ticker (normalized to upper).
            signal_type: SignalType constant, e.g. "RISK_SPIKE".
            hours:       Look-back window. Default 24h.
            user_id:     Optional — scope to specific user when provided.
        """
        from datetime import timedelta

        from src.watchlist.models import SignalEvent

        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        stmt = (
            select(SignalEvent.id)
            .where(SignalEvent.ticker == ticker.upper())
            .where(SignalEvent.signal_type == signal_type)
            .where(SignalEvent.occurred_at >= cutoff)
            .limit(1)
        )
        if user_id:
            stmt = stmt.where(SignalEvent.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None
