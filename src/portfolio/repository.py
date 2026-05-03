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
