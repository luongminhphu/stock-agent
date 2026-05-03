"""PortfolioService — write-side lifecycle for Position and Trade.

Owner: portfolio segment.

Responsibilities:
  - buy()        — open new or add to existing position, record Trade(BUY)
  - sell()       — reduce or close position, record Trade(SELL) with realized P&L
  - list_open()  — return all open positions for a user

Does NOT calculate P&L for display — that is PnlService (read concern).
Does NOT send Discord notifications — bot/adapter concern.

Partial sell:
  sell(qty < position.qty) reduces qty, keeps position open.
  sell(qty == position.qty) sets closed_at, position is fully closed.
  sell(qty > position.qty) raises InsufficientQtyError.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.portfolio.models import Position, Trade, TradeType
from src.portfolio.repository import PortfolioRepository

logger = get_logger(__name__)


class InsufficientQtyError(Exception):
    """Raised when sell qty exceeds current position qty."""


class PositionNotFoundError(Exception):
    """Raised when no open position exists for the given ticker."""


class PortfolioService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PortfolioRepository(session)

    # ------------------------------------------------------------------
    # Buy
    # ------------------------------------------------------------------

    async def buy(
        self,
        user_id: str,
        ticker: str,
        qty: float,
        price: float,
        thesis_id: int | None = None,
        note: str | None = None,
    ) -> tuple[Position, Trade]:
        """Open a new position or add to an existing one.

        avg_cost is recalculated as volume-weighted average:
            new_avg = (old_qty * old_avg + qty * price) / (old_qty + qty)

        Returns:
            (position, trade) — both flushed to DB, caller must commit.
        """
        ticker = ticker.upper()
        position = await self._repo.get_open_position(user_id, ticker)

        if position is None:
            position = Position(
                user_id=user_id,
                ticker=ticker,
                qty=qty,
                avg_cost=price,
                thesis_id=thesis_id,
                note=note,
                opened_at=datetime.now(UTC),
            )
        else:
            # Recalculate VWAP avg_cost
            total_cost = position.qty * position.avg_cost + qty * price
            position.qty += qty
            position.avg_cost = total_cost / position.qty
            if thesis_id is not None:
                position.thesis_id = thesis_id

        await self._repo.save_position(position)

        trade = Trade(
            user_id=user_id,
            ticker=ticker,
            position_id=position.id,
            trade_type=TradeType.BUY,
            qty=qty,
            price=price,
            realized_pnl=None,
            note=note,
            traded_at=datetime.now(UTC),
        )
        await self._repo.save_trade(trade)

        logger.info(
            "portfolio.bought",
            user_id=user_id,
            ticker=ticker,
            qty=qty,
            price=price,
            new_avg_cost=position.avg_cost,
            new_qty=position.qty,
        )
        return position, trade

    # ------------------------------------------------------------------
    # Sell
    # ------------------------------------------------------------------

    async def sell(
        self,
        user_id: str,
        ticker: str,
        qty: float,
        price: float,
        note: str | None = None,
    ) -> tuple[Position, Trade]:
        """Reduce or close an open position.

        realized_pnl = (price - avg_cost) * qty

        Raises:
            PositionNotFoundError: No open position for this ticker.
            InsufficientQtyError:  sell qty > current position qty.

        Returns:
            (position, trade) — position may be closed (closed_at set).
        """
        ticker = ticker.upper()
        position = await self._repo.get_open_position(user_id, ticker)

        if position is None:
            raise PositionNotFoundError(f"No open position for {ticker}")

        if qty > position.qty:
            raise InsufficientQtyError(
                f"Cannot sell {qty} of {ticker} — only {position.qty} held"
            )

        realized_pnl = (price - position.avg_cost) * qty
        position.realized_pnl += realized_pnl
        position.qty -= qty

        if position.qty == 0:
            position.closed_at = datetime.now(UTC)

        await self._repo.save_position(position)

        trade = Trade(
            user_id=user_id,
            ticker=ticker,
            position_id=position.id,
            trade_type=TradeType.SELL,
            qty=qty,
            price=price,
            realized_pnl=realized_pnl,
            note=note,
            traded_at=datetime.now(UTC),
        )
        await self._repo.save_trade(trade)

        logger.info(
            "portfolio.sold",
            user_id=user_id,
            ticker=ticker,
            qty=qty,
            price=price,
            realized_pnl=realized_pnl,
            position_closed=position.closed_at is not None,
        )
        return position, trade

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_open(self, user_id: str) -> list[Position]:
        """Return all open positions, ordered by ticker."""
        return await self._repo.list_open_positions(user_id)
