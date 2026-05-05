"""SQLAlchemy ORM models for the portfolio segment.

Owner: portfolio segment only.
Other segments MUST NOT import these models directly —
use PortfolioService or PnlService as the public interface.

Two ORM models:
  Position  — current state of a holding (qty, avg_cost, open/closed)
  Trade     — immutable record of each BUY/SELL execution

One read-model dataclass (no DB table):
  PortfolioContext  — typed snapshot consumed by ai/context_builder.
                      Built by get_portfolio_context() in __init__.py.

Relationship:
  One Position → many Trades.
  Position tracks the running state; Trade tracks the audit trail.

Partial sell example:
  Position(VCB, qty=200, avg_cost=87_500)
  → /sell VCB 100 91_000
  → Trade(SELL, qty=100, price=91_000, realized_pnl=350_000)
  → Position(VCB, qty=100, avg_cost=87_500)  — avg_cost unchanged
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.platform.db import Base

_values = lambda x: [e.value for e in x]  # noqa: E731


class TradeType(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"


class Position(Base):
    """Current state of a holding.

    avg_cost is the volume-weighted average cost across all BUY trades.
    Recalculated by PortfolioService on each buy.
    closed_at is set when qty reaches 0 (full close).

    sector is an optional free-text label (e.g. "tài chính", "nguyên vật liệu").
    Used by ContextBuilder._fetch_portfolio_bias() to compute sector weights.
    Nullable — positions created before this field existed remain valid.
    """

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cost: Mapped[float] = mapped_column(Float, nullable=False)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    thesis_id: Mapped[int | None] = mapped_column(Integer, index=True)
    note: Mapped[str | None] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    trades: Mapped[list[Trade]] = relationship(
        back_populates="position", cascade="all, delete-orphan", order_by="Trade.traded_at"
    )

    @property
    def is_open(self) -> bool:
        return self.closed_at is None and self.qty > 0

    def __repr__(self) -> str:
        return f"<Position user={self.user_id} ticker={self.ticker} qty={self.qty} avg={self.avg_cost}>"


class Trade(Base):
    """Immutable record of a single BUY or SELL execution.

    Never mutated after creation. Source of truth for trade history.
    realized_pnl is set only on SELL trades:
        realized_pnl = (price - position.avg_cost) * qty
    """

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    position_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("positions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trade_type: Mapped[TradeType] = mapped_column(
        SAEnum(TradeType, name="tradetype", create_constraint=False, values_callable=_values),
        nullable=False,
    )
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[float | None] = mapped_column(Float)  # SELL only
    traded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)

    position: Mapped[Position] = relationship(back_populates="trades")

    def __repr__(self) -> str:
        return (
            f"<Trade {self.trade_type} {self.ticker} qty={self.qty} "
            f"price={self.price} pnl={self.realized_pnl}>"
        )


# ---------------------------------------------------------------------------
# PortfolioContext — read-model dataclass (no DB table)
# ---------------------------------------------------------------------------

@dataclass
class PositionSummary:
    """Lightweight snapshot of one open position for AI context.

    Intentionally flat — no SQLAlchemy ORM objects cross the boundary.
    """

    ticker: str
    qty: float
    avg_cost: float
    sector: str | None
    thesis_id: int | None
    market_value: float | None = None   # filled by get_portfolio_context() if prices available
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None


@dataclass
class PortfolioContext:
    """Typed snapshot of a user's portfolio state.

    Owner: portfolio segment.
    Consumed by: ai/context_builder — import from portfolio.__init__ only.

    Contract:
      - Immutable after construction (dataclass, no setters).
      - Never contains SQLAlchemy ORM objects.
      - sector_weights: {sector_name: weight_pct} based on market_value
        (or avg_cost * qty when prices unavailable).
      - total_realized_pnl: sum of realized_pnl across ALL positions
        (open + closed) for the user — lifetime figure.

    Boundary rule:
      portfolio → ai is FORBIDDEN.
      ai → portfolio (read PortfolioContext) is ALLOWED.
    """

    user_id: str
    open_positions: list[PositionSummary] = field(default_factory=list)
    sector_weights: dict[str, float] = field(default_factory=dict)   # sector → weight %
    total_cost_basis: float = 0.0         # sum(avg_cost * qty) for open positions
    total_market_value: float | None = None  # None when prices unavailable
    total_unrealized_pnl: float | None = None
    total_realized_pnl: float = 0.0
    position_count: int = 0
    as_of: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def has_positions(self) -> bool:
        return self.position_count > 0

    @property
    def tickers(self) -> list[str]:
        return [p.ticker for p in self.open_positions]
