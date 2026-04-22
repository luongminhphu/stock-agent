"""Watchlist service — CRUD for watchlist items and alerts.

Owner: watchlist segment.
Bot commands and API routes use this; they do not import models directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.watchlist.models import (
    Alert,
    AlertConditionType,
    AlertStatus,
    WatchlistItem,
)
from src.watchlist.repository import WatchlistRepository

logger = get_logger(__name__)


@dataclass
class AddToWatchlistInput:
    user_id: str
    ticker: str
    note: str = ""
    thesis_id: int | None = None
    priority: int = 100


@dataclass
class CreateAlertInput:
    user_id: str
    ticker: str
    condition_type: AlertConditionType
    threshold: float
    note: str = ""
    watchlist_item_id: int | None = None


AddAlertInput = CreateAlertInput


class WatchlistItemNotFoundError(Exception): ...


class WatchlistItemAlreadyExistsError(Exception): ...


class AlertNotFoundError(Exception): ...


class WatchlistService:
    """Manage watchlist items and alerts for a user."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = WatchlistRepository(session)

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
            raise WatchlistItemNotFoundError(f"{ticker} not found in watchlist for user {user_id}")
        await self._repo.delete_item(item)
        logger.info("watchlist.removed", user_id=user_id, ticker=ticker)

    async def list_items(self, user_id: str) -> list[WatchlistItem]:
        return await self._repo.list_for_user(user_id)

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

    async def add_alert(self, inp: AddAlertInput) -> Alert:
        return await self.create_alert(inp)

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
