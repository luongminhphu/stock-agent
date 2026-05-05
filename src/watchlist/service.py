"""Watchlist service — CRUD for watchlist items and alerts.

Owner: watchlist segment.
Bot commands and API routes use this; they do not import models directly.

DTOs and Exceptions → dtos.py
"""

from __future__ import annotations

from dataclasses import dataclass

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
from src.watchlist.repository import WatchlistRepository

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
