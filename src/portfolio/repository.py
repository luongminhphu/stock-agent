"""PortfolioRepository — DB access for Position and Trade models.

Owner: portfolio segment.
Called only by PortfolioService and PnlService — never by bot/api directly.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.portfolio.models import Position, Trade, TradeType


class PortfolioRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Position
    # ------------------------------------------------------------------

    async def get_open_position(self, user_id: str, ticker: str) -> Position | None:
        """Return the open position for user+ticker, or None."""
        stmt = (
            select(Position)
            .where(Position.user_id == user_id)
            .where(Position.ticker == ticker.upper())
            .where(Position.closed_at.is_(None))
            .options(selectinload(Position.trades))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_position_by_id(self, position_id: int) -> Position | None:
        """Return a position by primary key."""
        stmt = (
            select(Position)
            .where(Position.id == position_id)
            .options(selectinload(Position.trades))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_open_positions(self, user_id: str) -> list[Position]:
        """Return all open positions for a user, ordered by ticker."""
        stmt = (
            select(Position)
            .where(Position.user_id == user_id)
            .where(Position.closed_at.is_(None))
            .order_by(Position.ticker)
            .options(selectinload(Position.trades))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def save_position(self, position: Position) -> None:
        self._session.add(position)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Trade
    # ------------------------------------------------------------------

    async def save_trade(self, trade: Trade) -> None:
        self._session.add(trade)
        await self._session.flush()

    async def get_trade_by_id(self, trade_id: int) -> Trade | None:
        """Return a trade by primary key."""
        stmt = select(Trade).where(Trade.id == trade_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_buy_trades(self, position_id: int) -> list[Trade]:
        """Return all BUY trades for a position, ordered by traded_at asc.

        Used by correct_trade() to recalculate VWAP avg_cost.
        """
        stmt = (
            select(Trade)
            .where(Trade.position_id == position_id)
            .where(Trade.trade_type == TradeType.BUY)
            .order_by(Trade.traded_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_trades(
        self,
        user_id: str,
        ticker: str | None = None,
        trade_type: TradeType | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[Trade]:
        """Return trade history for a user, newest first."""
        stmt = (
            select(Trade)
            .where(Trade.user_id == user_id)
            .order_by(Trade.traded_at.desc())
            .limit(limit)
        )
        if ticker:
            stmt = stmt.where(Trade.ticker == ticker.upper())
        if trade_type:
            stmt = stmt.where(Trade.trade_type == trade_type)
        if since:
            stmt = stmt.where(Trade.traded_at >= since)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_sell_trades(
        self,
        user_id: str,
        ticker: str | None = None,
        since: datetime | None = None,
    ) -> list[Trade]:
        """Return SELL trades only — used by PnlService for realized summary."""
        return await self.list_trades(
            user_id, ticker=ticker, trade_type=TradeType.SELL, since=since, limit=500
        )
