"""SQLAlchemy ORM models for the portfolio segment.

Owner: portfolio segment only.
Other segments MUST NOT import these models directly —
use PortfolioService or PnlService as the public interface.

Two models:
  Position  — current state of a holding (qty, avg_cost, open/closed)
  Trade     — immutable record of each BUY/SELL execution

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
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

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
    """

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cost: Mapped[float] = mapped_column(Float, nullable=False)
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
