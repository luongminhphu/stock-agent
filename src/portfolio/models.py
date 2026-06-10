"""SQLAlchemy ORM models for the portfolio segment.

Owner: portfolio segment only.
Other segments MUST NOT import these models directly —
use PortfolioService or PnlService as the public interface.

ORM models:
  Position        — current state of a holding (qty, avg_cost, open/closed)
  Trade           — immutable record of each BUY/SELL execution
  DividendRecord  — immutable record of each dividend received

Read-model dataclasses (no DB table):
  PortfolioContext  — typed snapshot consumed by ai/context_builder.
                      Built by get_portfolio_context() in __init__.py.

Relationship:
  One Position → many Trades.
  One Position → many DividendRecords.
  Position tracks the running state; Trade and DividendRecord track the audit trail.

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

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.platform.db import Base

_values = lambda x: [e.value for e in x]  # noqa: E731


class TradeType(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"


class DividendType(enum.StrEnum):
    CASH = "cash"
    STOCK = "stock"


class ExitReason(enum.StrEnum):
    """Why a SELL trade was executed.

    Used by ReplayAgent to contextualize post-mortem analysis and detect
    behavioral patterns (e.g. repeated stop_loss ignoring, early exits).

    Rules:
    - Set only on SELL trades (Trade.exit_reason). Nullable for BUY trades.
    - Caller (trade_usecase / bot command) is responsible for passing this
      value when recording a SELL. Defaults to MANUAL when not specified.
    - thesis_invalidated: one or more invalidation conditions were triggered.
    - target_hit: price reached the thesis target zone.
    - stop_loss: price hit the thesis stop_loss level.
    - time_decay: thesis time horizon expired without catalyst.
    - opportunity_cost: capital redeployed to a better setup.
    - manual: user-initiated without a specific systematic reason.
    """

    THESIS_INVALIDATED = "thesis_invalidated"
    TARGET_HIT         = "target_hit"
    STOP_LOSS          = "stop_loss"
    TIME_DECAY         = "time_decay"
    OPPORTUNITY_COST   = "opportunity_cost"
    MANUAL             = "manual"


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
    dividends: Mapped[list[DividendRecord]] = relationship(
        back_populates="position", cascade="all, delete-orphan", order_by="DividendRecord.paid_at"
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

    exit_reason (SELL only, nullable):
        Why the position was closed. Set by caller at SELL time.
        Used by ReplayAgent to contextualize post-mortem and detect
        recurring behavioral patterns. Defaults to None (backward compat).
        Use ExitReason.MANUAL when reason is not systematically tracked.

    entry_signal_ref (BUY only, nullable):
        Optional free-text reference to the brief snapshot ID, signal ID,
        or watchlist alert that triggered this buy decision.
        Format: free string, e.g. "brief:2024-03-15", "signal:VCB-breakout-42".
        Used by ReplayAgent to reconstruct the decision context for replay.
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
    exit_reason: Mapped[ExitReason | None] = mapped_column(
        SAEnum(
            ExitReason,
            name="exitreason",
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=True,
        comment="SELL only — why position was closed. See ExitReason enum.",
    )
    entry_signal_ref: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="BUY only — optional ref to brief/signal that triggered entry.",
    )

    position: Mapped[Position] = relationship(back_populates="trades")

    def __repr__(self) -> str:
        return (
            f"<Trade {self.trade_type} {self.ticker} qty={self.qty} "
            f"price={self.price} pnl={self.realized_pnl}>"
        )


class DividendRecord(Base):
    """Immutable record of a dividend received for a position.

    Never mutated after creation. Source of truth for dividend history.

    total_amount = qty * dividend_per_share (computed at record time).

    position_id is nullable — allows recording dividend for a ticker
    that no longer has an open position (e.g. closed before ex_date settles).

    dividend_type:
      - cash  : tiền mặt (VND per share)
      - stock : cổ tức bằng cổ phiếu (tỷ lệ %, VD: 0.10 = 10%)

    ex_date: ngày chốt quyền (ex-dividend date). Nullable — user may omit.
    paid_at: thời điểm ghi nhận thực tế (UTC, set automatically).
    """

    __tablename__ = "dividend_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    position_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("positions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    dividend_per_share: Mapped[float] = mapped_column(Float, nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    dividend_type: Mapped[DividendType] = mapped_column(
        SAEnum(DividendType, name="dividendtype", create_constraint=False, values_callable=_values),
        nullable=False,
        default=DividendType.CASH,
    )
    ex_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)

    position: Mapped[Position | None] = relationship(back_populates="dividends")

    def __repr__(self) -> str:
        return (
            f"<DividendRecord {self.ticker} qty={self.qty} "
            f"dps={self.dividend_per_share} total={self.total_amount}>"
        )


# ---------------------------------------------------------------------------
# PositionDailySnapshot — EOD persistent P&L per position
# ---------------------------------------------------------------------------


class PositionDailySnapshot(Base):
    """End-of-day P&L snapshot for a single position.

    Written once per trading day per (user_id, ticker) by EodSnapshotService at 15:20 ICT.
    Used as primary source for portfolio dashboard — independent of QuoteService availability.

    Invariants:
      - UNIQUE(user_id, ticker, snapshot_date): upsert on conflict.
      - close_price = giá đóng cửa thực tế từ QuoteService.
      - unrealized_pnl = (close_price - avg_cost) * qty.
      - qty / avg_cost copied from Position at snapshot time (immutable audit trail).
    """

    __tablename__ = "position_daily_snapshots"
    __table_args__ = (
        UniqueConstraint("user_id", "ticker", "snapshot_date", name="uq_position_daily_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    snapshot_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cost: Mapped[float] = mapped_column(Float, nullable=False)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    cost_basis: Mapped[float] = mapped_column(Float, nullable=False)
    market_value: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pct: Mapped[float] = mapped_column(Float, nullable=False)
    thesis_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<PositionDailySnapshot {self.ticker} {self.snapshot_date} "
            f"close={self.close_price} pnl={self.unrealized_pnl}>"
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

    def format_for_prompt(self) -> str:
        """Render PortfolioContext as a human-readable block for AI prompt injection.

        Used exclusively by ai/context_builder._fetch_portfolio_bias().
        Never returns raw dataclass repr.

        Output example::

            Portfolio (3 vị thế mở):
              - VCB: 500 cp @ 87,500 | lãi/lỗ TT: +1,750,000 (+4.0%) [Banking]
              - HPG: 1,000 cp @ 28,000 [Materials]
              Tỷ trọng ngành: Banking: 62.5%, Materials: 37.5%
              Lãi/lỗ TT tổng: +1,750,000
              Realized P&L: +3,200,000
        """
        if not self.has_positions:
            return "Portfolio: Chưa có vị thế nào đang mở."

        lines = [f"Portfolio ({self.position_count} vị thế mở):"]

        for p in self.open_positions:
            base = f"  - {p.ticker}: {p.qty:,.0f} cp @ {p.avg_cost:,.0f}"
            if p.unrealized_pnl is not None and p.unrealized_pnl_pct is not None:
                sign = "+" if p.unrealized_pnl >= 0 else ""
                base += f" | lãi/lỗ TT: {sign}{p.unrealized_pnl:,.0f} ({sign}{p.unrealized_pnl_pct:.1f}%)"
            elif p.unrealized_pnl is not None:
                sign = "+" if p.unrealized_pnl >= 0 else ""
                base += f" | lãi/lỗ TT: {sign}{p.unrealized_pnl:,.0f}"
            if p.sector:
                base += f" [{p.sector}]"
            lines.append(base)

        if self.sector_weights:
            weights = ", ".join(
                f"{k}: {v:.1f}%" for k, v in sorted(
                    self.sector_weights.items(), key=lambda x: x[1], reverse=True
                )
            )
            lines.append(f"  Tỷ trọng ngành: {weights}")

        if self.total_unrealized_pnl is not None:
            sign = "+" if self.total_unrealized_pnl >= 0 else ""
            lines.append(f"  Lãi/lỗ TT tổng: {sign}{self.total_unrealized_pnl:,.0f}")

        if self.total_realized_pnl:
            sign = "+" if self.total_realized_pnl >= 0 else ""
            lines.append(f"  Realized P&L: {sign}{self.total_realized_pnl:,.0f}")

        return "\n".join(lines)
