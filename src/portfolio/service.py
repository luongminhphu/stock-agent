"""PortfolioService — write-side lifecycle for Position, Trade, and DividendRecord.

Owner: portfolio segment.

Responsibilities:
  - buy()              — open new or add to existing position, record Trade(BUY)
  - sell()             — reduce or close position, record Trade(SELL) with realized P&L
  - correct_trade()    — fix price of a BUY trade and recalculate position avg_cost (VWAP)
  - record_dividend()  — record a cash or stock dividend received for a ticker
  - list_open()        — return all open positions for a user

Does NOT calculate P&L for display — that is PnlService (read concern).
Does NOT send Discord notifications — bot/adapter concern.

Partial sell:
  sell(qty < position.qty) reduces qty, keeps position open.
  sell(qty == position.qty) sets closed_at, position is fully closed.
  sell(qty > position.qty) raises InsufficientQtyError.

correct_trade():
  Only BUY trades can be corrected (SELL realized P&L is already settled).
  Recalculates position.avg_cost as VWAP from all BUY trades after correction.

record_dividend():
  Accepts cash (VND/share) or stock (ratio, e.g. 0.10 = 10%) dividends.
  Looks up open position to link position_id — position_id is nullable if
  ticker no longer has an open position (allowed for backdated entries).
  total_amount = qty * dividend_per_share (meaningful for cash; informational for stock).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.portfolio.models import DividendRecord, DividendType, Position, Trade, TradeType
from src.portfolio.repository import PortfolioRepository

logger = get_logger(__name__)

# Positions with qty below this threshold are treated as fully closed.
_QTY_ZERO_EPSILON = 1e-9


class InsufficientQtyError(Exception):
    """Raised when sell qty exceeds current position qty."""


class PositionNotFoundError(Exception):
    """Raised when no open position exists for the given ticker."""


class TradeNotFoundError(Exception):
    """Raised when trade_id does not exist or does not belong to user."""


class InvalidOperationError(Exception):
    """Raised when the requested operation is not valid for the trade/position state."""


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
        sector: str | None = None,
    ) -> tuple[Position, Trade]:
        """Open a new position or add to an existing one.

        avg_cost is recalculated as volume-weighted average:
            new_avg = (old_qty * old_avg + qty * price) / (old_qty + qty)

        sector is an optional free-text label (e.g. "tài chính",
        "nguyên vật liệu"). Stored on Position for use by
        ContextBuilder._fetch_portfolio_bias(). Omit to leave unchanged
        on an existing position, or NULL on a new one.

        Raises:
            ValueError: qty or price is not positive.

        Returns:
            (position, trade) — both flushed to DB, caller must commit.
        """
        if qty <= 0:
            raise ValueError(f"qty phải lớn hơn 0, nhận được: {qty}")
        if price <= 0:
            raise ValueError(f"price phải lớn hơn 0, nhận được: {price}")

        ticker = ticker.upper()
        position = await self._repo.get_open_position(user_id, ticker)

        if position is None:
            position = Position(
                user_id=user_id,
                ticker=ticker,
                qty=qty,
                avg_cost=price,
                sector=sector,
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
            if sector is not None:
                position.sector = sector

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
            sector=sector,
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
            ValueError:            qty or price is not positive.
            PositionNotFoundError: No open position for this ticker.
            InsufficientQtyError:  sell qty > current position qty.

        Returns:
            (position, trade) — position may be closed (closed_at set).
        """
        if qty <= 0:
            raise ValueError(f"qty phải lớn hơn 0, nhận được: {qty}")
        if price <= 0:
            raise ValueError(f"price phải lớn hơn 0, nhận được: {price}")

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

        if position.qty <= _QTY_ZERO_EPSILON:
            position.qty = 0.0
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
    # Correct trade
    # ------------------------------------------------------------------

    async def correct_trade(
        self,
        user_id: str,
        trade_id: int,
        new_price: float,
    ) -> tuple[Position, Trade]:
        """Correct the price of a BUY trade and recalculate position avg_cost.

        Only BUY trades on open positions can be corrected.
        SELL trades are excluded because realized_pnl has already been settled
        and changing the sell price retroactively would distort accounting.

        Process:
          1. Load trade — verify ownership and trade_type == BUY.
          2. Verify the parent position is still open (closed_at is None).
          3. Update trade.price = new_price.
          4. Reload all BUY trades for the position and recalculate VWAP avg_cost.
          5. Save both trade and position.

        Raises:
            ValueError:            new_price is not positive.
            TradeNotFoundError:    trade_id not found or belongs to another user.
            InvalidOperationError: trade is not BUY, or position is already closed.

        Returns:
            (position, trade) — both flushed, caller must commit.
        """
        if new_price <= 0:
            raise ValueError(f"new_price phải lớn hơn 0, nhận được: {new_price}")

        trade = await self._repo.get_trade_by_id(trade_id)

        if trade is None or trade.user_id != user_id:
            raise TradeNotFoundError(f"Trade #{trade_id} not found.")

        if trade.trade_type != TradeType.BUY:
            raise InvalidOperationError(
                "Chỉ có thể sửa BUY trade. "
                "SELL trade đã được hạch toán realized P&L và không thể chỉnh sửa."
            )

        position = await self._repo.get_position_by_id(trade.position_id)
        if position is None or position.closed_at is not None:
            raise InvalidOperationError(
                f"Vị thế #{trade.position_id} đã đóng — không thể sửa trade thuộc vị thế đã closed."
            )

        old_price = trade.price
        trade.price = new_price
        await self._repo.save_trade(trade)

        buy_trades = await self._repo.list_buy_trades(position.id)
        total_cost = sum(t.price * t.qty for t in buy_trades)
        total_qty = sum(t.qty for t in buy_trades)
        position.avg_cost = total_cost / total_qty if total_qty > 0 else new_price
        await self._repo.save_position(position)

        logger.info(
            "portfolio.trade_corrected",
            user_id=user_id,
            trade_id=trade_id,
            ticker=trade.ticker,
            old_price=old_price,
            new_price=new_price,
            new_avg_cost=position.avg_cost,
        )
        return position, trade

    # ------------------------------------------------------------------
    # Dividend
    # ------------------------------------------------------------------

    async def record_dividend(
        self,
        user_id: str,
        ticker: str,
        qty: float,
        dividend_per_share: float,
        dividend_type: DividendType = DividendType.CASH,
        ex_date: datetime | None = None,
        note: str | None = None,
    ) -> DividendRecord:
        """Record a dividend received for a ticker.

        total_amount = qty * dividend_per_share.
        For cash dividends: dividend_per_share is VND per share.
        For stock dividends: dividend_per_share is the ratio (e.g. 0.10 = 10%).

        Automatically links to the open position if one exists.
        position_id is nullable — recording against a closed position is allowed.

        Raises:
            ValueError: qty or dividend_per_share is not positive.

        Returns:
            DividendRecord — flushed to DB, caller must commit.
        """
        if qty <= 0:
            raise ValueError(f"qty phải lớn hơn 0, nhận được: {qty}")
        if dividend_per_share <= 0:
            raise ValueError(f"dividend_per_share phải lớn hơn 0, nhận được: {dividend_per_share}")

        ticker = ticker.upper()
        position = await self._repo.get_open_position(user_id, ticker)
        position_id = position.id if position is not None else None
        total_amount = qty * dividend_per_share

        record = DividendRecord(
            user_id=user_id,
            ticker=ticker,
            position_id=position_id,
            qty=qty,
            dividend_per_share=dividend_per_share,
            total_amount=total_amount,
            dividend_type=dividend_type,
            ex_date=ex_date,
            note=note,
            paid_at=datetime.now(UTC),
        )
        await self._repo.save_dividend(record)

        logger.info(
            "portfolio.dividend_recorded",
            user_id=user_id,
            ticker=ticker,
            qty=qty,
            dividend_per_share=dividend_per_share,
            dividend_type=dividend_type.value,
            total_amount=total_amount,
            position_id=position_id,
        )
        return record

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_open(self, user_id: str) -> list[Position]:
        """Return all open positions, ordered by ticker."""
        return await self._repo.list_open_positions(user_id)
