"""SQLAlchemy ORM models for the thesis segment.

Owner: thesis segment only.
No other segment imports these models directly;
they access thesis data through ThesisService or read via readmodel.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.platform.db import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enum_values(x):
    """Return .value list for SAEnum so asyncpg binds lowercase strings."""
    return [e.value for e in x]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ThesisStatus(enum.StrEnum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    CLOSED = "closed"
    PAUSED = "paused"


class AssumptionStatus(enum.StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNCERTAIN = "uncertain"
    PENDING = "pending"  # not yet assessed


class CatalystStatus(enum.StrEnum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ReviewVerdict(enum.StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    WATCHLIST = "WATCHLIST"


class DecisionType(enum.StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    ADD = "ADD"
    REDUCE = "REDUCE"


class OutcomeVerdict(enum.StrEnum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    MIXED = "MIXED"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Thesis(Base):
    __tablename__ = "theses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ThesisStatus] = mapped_column(
        SAEnum(ThesisStatus, values_callable=_enum_values),
        default=ThesisStatus.ACTIVE,
        index=True,
    )
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    assumptions: Mapped[list[Assumption]] = relationship(
        back_populates="thesis", cascade="all, delete-orphan"
    )
    catalysts: Mapped[list[Catalyst]] = relationship(
        back_populates="thesis", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list[ThesisSnapshot]] = relationship(
        back_populates="thesis", cascade="all, delete-orphan"
    )
    reviews: Mapped[list[ThesisReview]] = relationship(
        back_populates="thesis", cascade="all, delete-orphan"
    )
    decision_logs: Mapped[list[DecisionLog]] = relationship(
        back_populates="thesis", cascade="all, delete-orphan"
    )


class Assumption(Base):
    __tablename__ = "assumptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("theses.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[AssumptionStatus] = mapped_column(
        SAEnum(AssumptionStatus, values_callable=_enum_values),
        default=AssumptionStatus.PENDING,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    thesis: Mapped[Thesis] = relationship(back_populates="assumptions")


class Catalyst(Base):
    __tablename__ = "catalysts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("theses.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(Text)
    expected_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[CatalystStatus] = mapped_column(
        SAEnum(CatalystStatus, values_callable=_enum_values),
        default=CatalystStatus.PENDING,
        index=True,
    )

    thesis: Mapped[Thesis] = relationship(back_populates="catalysts")


class ThesisSnapshot(Base):
    __tablename__ = "thesis_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("theses.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float)
    verdict: Mapped[ReviewVerdict] = mapped_column(
        SAEnum(ReviewVerdict, values_callable=_enum_values)
    )
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    thesis: Mapped[Thesis] = relationship(back_populates="snapshots")


class ThesisReview(Base):
    __tablename__ = "thesis_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("theses.id", ondelete="CASCADE"), index=True)
    summary: Mapped[str] = mapped_column(Text)
    verdict: Mapped[ReviewVerdict] = mapped_column(
        SAEnum(ReviewVerdict, values_callable=_enum_values), index=True
    )
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    thesis: Mapped[Thesis] = relationship(back_populates="reviews")


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int] = mapped_column(
        ForeignKey("theses.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    decision_type: Mapped[DecisionType] = mapped_column(
        SAEnum(DecisionType, values_callable=_enum_values), index=True
    )
    decision_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    price_at_decision: Mapped[float | None] = mapped_column(Float, nullable=True)
    thesis_score_at_decision: Mapped[float | None] = mapped_column(Float, nullable=True)
    thesis_health_score_at_decision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_signal: Mapped[str | None] = mapped_column(String(100), nullable=True)
    brief_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str] = mapped_column(Text)
    review_horizon_days: Mapped[int] = mapped_column(Integer, default=30)

    outcome_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    outcome_verdict: Mapped[OutcomeVerdict | None] = mapped_column(
        SAEnum(OutcomeVerdict, values_callable=_enum_values), nullable=True, index=True
    )

    thesis: Mapped[Thesis] = relationship(back_populates="decision_logs")
