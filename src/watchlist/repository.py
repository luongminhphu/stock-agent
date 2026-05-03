"""Watchlist repository — async DB access for watchlist segment.

Owner: watchlist segment.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.watchlist.models import Alert, AlertStatus, Reminder, WatchlistItem, WatchlistScan


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
