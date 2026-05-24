"""PnlService — read-side P&L calculations for the portfolio segment.

Owner: portfolio segment (read concern).
Consumes QuoteService for realtime prices.

Does NOT mutate any DB state — pure read + calculation.

Outputs:
  PositionPnl       — unrealized P&L for a single open position
  PortfolioPnl      — aggregated view of all open positions
  RealizedSummary   — realized P&L stats from trade history
  DividendSummary   — dividend totals and records per user/ticker
  DividendSnapshot  — plain dataclass snapshot of a DividendRecord row
  TradeSnapshot     — plain dataclass snapshot of a Trade row (safe outside session)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.portfolio.models import DividendType, Position
from src.portfolio.repository import PortfolioRepository

logger = get_logger(__name__)

_BREAKEVEN_EPSILON = 1.0
_TRADE_HISTORY_MAX_LIMIT = 200
_DIVIDEND_HISTORY_MAX_LIMIT = 100


@runtime_checkable
class QuoteServiceProtocol(Protocol):
    """Minimal contract PnlService cần từ market segment."""

    async def get_quote(self, ticker: str) -> object:
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
class DividendSnapshot:
    """Plain-data snapshot of a DividendRecord row.

    Copied from ORM while session is open — safe to access after session close.
    """

    id: int
    ticker: str
    qty: float
    dividend_per_share: float
    total_amount: float
    dividend_type: str          # "cash" or "stock"
    ex_date: datetime | None
    paid_at: datetime
    note: str | None


@dataclass
class DividendSummary:
    """Aggregated dividend data for a user (optionally per ticker)."""

    total_cash_received: float
    record_count: int
    records: list[DividendSnapshot] = field(default_factory=list)
    ticker: str | None = None   # None = all tickers


@dataclass
class TradeSnapshot:
    """Plain-data snapshot of a Trade row."""

    id: int
    ticker: str
    trade_type: str
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
        if not isinstance(quote_service, QuoteServiceProtocol):
            raise TypeError(
                f"quote_service must implement QuoteServiceProtocol "
                f"(has get_quote method), got: {type(quote_service).__name__}"
            )
        self._session = session
        self._repo = PortfolioRepository(session)
        self._quote_service = quote_service

    async def get_portfolio_pnl(self, user_id: str) -> PortfolioPnl:
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

    async def get_dividend_summary(
        self,
        user_id: str,
        ticker: str | None = None,
        limit: int = 20,
    ) -> DividendSummary:
        """Return dividend history and total cash received for a user.

        limit is clamped to [1, _DIVIDEND_HISTORY_MAX_LIMIT].
        Only cash dividends count toward total_cash_received.
        Stock dividends appear in records but are excluded from the cash total.
        """
        limit = max(1, min(limit, _DIVIDEND_HISTORY_MAX_LIMIT))
        records = await self._repo.list_dividends(user_id, ticker=ticker, limit=limit)
        total_cash = await self._repo.get_dividend_total(user_id, ticker=ticker)

        snapshots = [
            DividendSnapshot(
                id=r.id,
                ticker=r.ticker,
                qty=r.qty,
                dividend_per_share=r.dividend_per_share,
                total_amount=r.total_amount,
                dividend_type=str(r.dividend_type).lower(),
                ex_date=r.ex_date,
                paid_at=r.paid_at,
                note=r.note,
            )
            for r in records
        ]
        return DividendSummary(
            total_cash_received=total_cash,
            record_count=len(snapshots),
            records=snapshots,
            ticker=ticker.upper() if ticker else None,
        )

    async def get_trade_history(
        self,
        user_id: str,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[TradeSnapshot]:
        limit = max(1, min(limit, _TRADE_HISTORY_MAX_LIMIT))
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
        if position.qty <= 0 or position.avg_cost <= 0:
            raise ValueError(
                f"Corrupt position data for {position.ticker}: "
                f"qty={position.qty}, avg_cost={position.avg_cost} — "
                "both must be positive."
            )
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
