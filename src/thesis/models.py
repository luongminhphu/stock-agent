"""SQLAlchemy ORM models for the thesis segment.

Owner: thesis segment only.
No other segment imports these models directly;
they access thesis data through ThesisService or read via readmodel.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
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
    PENDING = "pending"


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


class RecommendationStatus(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


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
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

    @property
    def risk_reward(self) -> float | None:
        """Computed risk/reward ratio: upside / downside.

        Returns None when any required price field is missing,
        or when downside <= 0 (stop_loss >= entry_price).
        """
        if (
            self.entry_price is not None
            and self.target_price is not None
            and self.stop_loss is not None
        ):
            upside = self.target_price - self.entry_price
            downside = self.entry_price - self.stop_loss
            if downside > 0:
                return round(upside / downside, 2)
        return None


class Assumption(Base):
    __tablename__ = "assumptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("theses.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[AssumptionStatus] = mapped_column(
        SAEnum(AssumptionStatus, values_callable=_enum_values),
        default=AssumptionStatus.PENDING,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    thesis: Mapped[Thesis] = relationship(back_populates="assumptions")


class Catalyst(Base):
    __tablename__ = "catalysts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("theses.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text)
    # DB column is expected_date (DateTime), NOT expected_by
    expected_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # Market snapshot fields (original — 0001)
    price_at_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_at_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    snapshotted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # Review-triggered snapshot fields (added in 0007)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Conviction timeline breakdown (added in 0012)
    score_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)

    thesis: Mapped[Thesis] = relationship(back_populates="snapshots")


class ThesisReview(Base):
    __tablename__ = "thesis_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("theses.id", ondelete="CASCADE"), index=True)
    verdict: Mapped[ReviewVerdict] = mapped_column(
        SAEnum(ReviewVerdict, values_callable=_enum_values), index=True
    )
    confidence: Mapped[float] = mapped_column(Float)
    reasoning: Mapped[str] = mapped_column(Text)
    risk_signals: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_watch_items: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    reviewed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # summary kept for backward compat with old code that sets it
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    thesis: Mapped[Thesis] = relationship(back_populates="reviews")
    recommendations: Mapped[list[ReviewRecommendation]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )


class ReviewRecommendation(Base):
    __tablename__ = "review_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        ForeignKey("thesis_reviews.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[RecommendationStatus] = mapped_column(
        SAEnum(RecommendationStatus, values_callable=_enum_values),
        default=RecommendationStatus.PENDING,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    review: Mapped[ThesisReview] = relationship(back_populates="recommendations")


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

    # AI lesson fields — written by DecisionService.persist_lesson() after ReplayAgent analysis
    key_lesson: Mapped[str | None] = mapped_column(Text, nullable=True)
    pattern_detected: Mapped[str | None] = mapped_column(String(100), nullable=True)

    thesis: Mapped[Thesis] = relationship(back_populates="decision_logs")
