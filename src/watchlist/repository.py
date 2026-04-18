"""Watchlist repository — async DB access for watchlist segment.

Owner: watchlist segment.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.watchlist.models import Alert, AlertStatus, WatchlistItem


class WatchlistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
