"""PnlService — read-side P&L calculations for the portfolio segment.

Owner: portfolio segment (read concern).
Consumes QuoteService for realtime prices.

Does NOT mutate any DB state — pure read + calculation.

Outputs:
  PositionPnl       — unrealized P&L for a single open position
  PortfolioPnl      — aggregated view of all open positions
  RealizedSummary   — realized P&L stats from trade history
  TradeSnapshot     — plain dataclass snapshot of a Trade row (safe outside session)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.portfolio.models import Position
from src.portfolio.repository import PortfolioRepository

logger = get_logger(__name__)

# Threshold dưới đây coi là hòa vốn (tránh float == 0.0 với realized_pnl VND).
# 1.0 VND là đủ nhỏ để không nhầm với lời/lỗ thực tế.
_BREAKEVEN_EPSILON = 1.0


@runtime_checkable
class QuoteServiceProtocol(Protocol):
    """Minimal contract PnlService cần từ market segment.

    Dùng Protocol để giữ loose coupling — market segment không cần
    import portfolio và portfolio không cần import class cụ thể.
    """

    async def get_quote(self, ticker: str) -> object:
        """Trả về object có thuộc tính .price (float)."""
        ...


@dataclass
class PositionPnl:
    """Unrealized P&L snapshot for a single open position."""

    ticker: str
    qty: float
    avg_cost: float
    current_price: float
    unrealized_pnl: float
    unrealized_pct: float
    market_value: float
    cost_basis: float
    thesis_id: int | None


@dataclass
class PortfolioPnl:
    """Aggregated P&L across all open positions."""

    positions: list[PositionPnl] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def total_cost_basis(self) -> float:
        return sum(p.cost_basis for p in self.positions)

    @property
    def total_market_value(self) -> float:
        return sum(p.market_value for p in self.positions)

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions)

    @property
    def total_unrealized_pct(self) -> float:
        if self.total_cost_basis == 0:
            return 0.0
        return self.total_unrealized_pnl / self.total_cost_basis * 100


@dataclass
class RealizedSummary:
    """Summary of realized P&L from closed/partial trades."""

    total_realized_pnl: float
    win_trades: int
    loss_trades: int
    breakeven_trades: int
    since: datetime | None

    @property
    def total_trades(self) -> int:
        return self.win_trades + self.loss_trades + self.breakeven_trades

    @property
    def win_rate(self) -> float | None:
        if self.total_trades == 0:
            return None
        return self.win_trades / self.total_trades


@dataclass
class TradeSnapshot:
    """Plain-data snapshot of a Trade row.

    Copied from the ORM object while the session is still open,
    so callers can safely access all fields after session close
    without risking DetachedInstanceError or lazy-load failures.
    """

    id: int
    ticker: str
    trade_type: str          # "buy" or "sell" (raw string, always lowercase)
    qty: float
    price: float
    realized_pnl: float | None
    traded_at: datetime | None
    note: str | None


class PnlService:
    """Read-side P&L calculations. No DB writes."""

    def __init__(self, session: AsyncSession, quote_service: QuoteServiceProtocol) -> None:
        if quote_service is None:
            raise ValueError(
                "PnlService requires a QuoteServiceProtocol instance. "
                "Pass quote_service=get_quote_service() from bootstrap."
            )
        self._session = session
        self._repo = PortfolioRepository(session)
        self._quote_service = quote_service

    async def get_portfolio_pnl(self, user_id: str) -> PortfolioPnl:
        """Fetch all open positions and calculate unrealized P&L with live prices."""
        positions = await self._repo.list_open_positions(user_id)
        result = PortfolioPnl()

        for pos in positions:
            try:
                pnl = await self._calc_position_pnl(pos)
                result.positions.append(pnl)
            except Exception as exc:
                logger.warning("pnl.fetch_error", ticker=pos.ticker, error=str(exc))
                result.errors[pos.ticker] = str(exc)

        return result

    async def get_position_pnl(self, user_id: str, ticker: str) -> PositionPnl | None:
        """Calculate unrealized P&L for a single ticker. Returns None if no open position."""
        position = await self._repo.get_open_position(user_id, ticker.upper())
        if position is None:
            return None
        return await self._calc_position_pnl(position)

    async def get_realized_summary(
        self,
        user_id: str,
        ticker: str | None = None,
        since: datetime | None = None,
    ) -> RealizedSummary:
        """Aggregate realized P&L from SELL trade history."""
        trades = await self._repo.list_sell_trades(user_id, ticker=ticker, since=since)

        total_pnl = 0.0
        wins = losses = breakevens = 0

        for trade in trades:
            pnl = trade.realized_pnl or 0.0
            total_pnl += pnl
            if pnl > _BREAKEVEN_EPSILON:
                wins += 1
            elif pnl < -_BREAKEVEN_EPSILON:
                losses += 1
            else:
                breakevens += 1

        return RealizedSummary(
            total_realized_pnl=total_pnl,
            win_trades=wins,
            loss_trades=losses,
            breakeven_trades=breakevens,
            since=since,
        )

    async def get_trade_history(
        self,
        user_id: str,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[TradeSnapshot]:
        """Return recent trade history as plain TradeSnapshot objects.

        Snapshots all columns eagerly while the session is open so callers
        can safely iterate after session close without DetachedInstanceError.
        """
        trades = await self._repo.list_trades(user_id, ticker=ticker, limit=limit)
        return [
            TradeSnapshot(
                id=t.id,
                ticker=t.ticker,
                trade_type=str(t.trade_type).lower(),
                qty=t.qty,
                price=t.price,
                realized_pnl=t.realized_pnl,
                traded_at=t.traded_at,
                note=t.note,
            )
            for t in trades
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _calc_position_pnl(self, position: Position) -> PositionPnl:
        quote = await self._quote_service.get_quote(position.ticker)
        current_price = quote.price  # type: ignore[union-attr]
        unrealized_pnl = (current_price - position.avg_cost) * position.qty
        cost_basis = position.avg_cost * position.qty
        unrealized_pct = (unrealized_pnl / cost_basis * 100) if cost_basis else 0.0

        return PositionPnl(
            ticker=position.ticker,
            qty=position.qty,
            avg_cost=position.avg_cost,
            current_price=current_price,
            unrealized_pnl=unrealized_pnl,
            unrealized_pct=unrealized_pct,
            market_value=current_price * position.qty,
            cost_basis=cost_basis,
            thesis_id=position.thesis_id,
        )
