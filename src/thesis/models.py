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
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
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


class ThesisStatus(str, enum.Enum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    CLOSED = "closed"
    PAUSED = "paused"


class AssumptionStatus(str, enum.Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNCERTAIN = "uncertain"
    PENDING = "pending"  # not yet assessed


class CatalystStatus(str, enum.Enum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ReviewVerdict(str, enum.Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    WATCHLIST = "WATCHLIST"


class RecommendationStatus(str, enum.Enum):
    """Lifecycle của một AI recommendation.

    PENDING   — AI vừa tạo, chờ user xác nhận.
    ACCEPTED  — User đã apply (assumption/catalyst đã được cập nhật).
    REJECTED  — User từ chối, không apply.
    EXPIRED   — Review mới hơn đã supersede recommendation này.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RecommendationTargetType(str, enum.Enum):
    ASSUMPTION = "assumption"
    CATALYST = "catalyst"


# ---------------------------------------------------------------------------
# Thesis
# ---------------------------------------------------------------------------


class Thesis(Base):
    __tablename__ = "theses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ThesisStatus] = mapped_column(
        SAEnum(ThesisStatus, values_callable=_enum_values),
        nullable=False,
        default=ThesisStatus.ACTIVE,
    )

    # Prices (VND)
    entry_price: Mapped[float | None] = mapped_column(Float)
    target_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)

    # Scoring (0-100, computed by ScoringService)
    score: Mapped[float | None] = mapped_column(Float)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    assumptions: Mapped[list[Assumption]] = relationship(
        back_populates="thesis", cascade="all, delete-orphan"
    )
    catalysts: Mapped[list[Catalyst]] = relationship(
        back_populates="thesis", cascade="all, delete-orphan"
    )
    reviews: Mapped[list[ThesisReview]] = relationship(
        back_populates="thesis", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list[ThesisSnapshot]] = relationship(
        back_populates="thesis", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Thesis id={self.id} ticker={self.ticker} status={self.status}>"

    # ------------------------------------------------------------------
    # Domain helpers (pure, no DB calls)
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self.status == ThesisStatus.ACTIVE

    @property
    def invalid_assumption_count(self) -> int:
        return sum(1 for a in self.assumptions if a.status == AssumptionStatus.INVALID)

    @property
    def triggered_catalyst_count(self) -> int:
        return sum(1 for c in self.catalysts if c.status == CatalystStatus.TRIGGERED)

    @property
    def upside_pct(self) -> float | None:
        """Potential upside from entry to target, in %."""
        if self.entry_price and self.target_price and self.entry_price > 0:
            return (self.target_price - self.entry_price) / self.entry_price * 100
        return None

    @property
    def risk_reward(self) -> float | None:
        """Risk/reward ratio: upside / downside."""
        if (
            self.entry_price
            and self.target_price
            and self.stop_loss
            and self.entry_price > self.stop_loss
        ):
            upside = self.target_price - self.entry_price
            downside = self.entry_price - self.stop_loss
            if downside > 0:
                return upside / downside
        return None


# ---------------------------------------------------------------------------
# Assumption
# ---------------------------------------------------------------------------


class Assumption(Base):
    __tablename__ = "assumptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("theses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AssumptionStatus] = mapped_column(
        SAEnum(AssumptionStatus, values_callable=_enum_values),
        nullable=False,
        default=AssumptionStatus.PENDING,
    )
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationship
    thesis: Mapped[Thesis] = relationship(back_populates="assumptions")

    def __repr__(self) -> str:
        return f"<Assumption id={self.id} status={self.status}>"


# ---------------------------------------------------------------------------
# Catalyst
# ---------------------------------------------------------------------------


class Catalyst(Base):
    __tablename__ = "catalysts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("theses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[CatalystStatus] = mapped_column(
        SAEnum(CatalystStatus, values_callable=_enum_values),
        nullable=False,
        default=CatalystStatus.PENDING,
    )
    expected_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)

    # Relationship
    thesis: Mapped[Thesis] = relationship(back_populates="catalysts")

    def __repr__(self) -> str:
        return f"<Catalyst id={self.id} status={self.status}>"


# ---------------------------------------------------------------------------
# ThesisReview (AI review snapshot)
# ---------------------------------------------------------------------------


class ThesisReview(Base):
    __tablename__ = "thesis_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("theses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    verdict: Mapped[ReviewVerdict] = mapped_column(
        SAEnum(ReviewVerdict, values_callable=_enum_values), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    risk_signals: Mapped[str | None] = mapped_column(Text)  # JSON list stored as text
    next_watch_items: Mapped[str | None] = mapped_column(Text)  # JSON list
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_price: Mapped[float | None] = mapped_column(Float)

    # Relationships
    thesis: Mapped[Thesis] = relationship(back_populates="reviews")
    recommendations: Mapped[list[ReviewRecommendation]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<ThesisReview id={self.id} verdict={self.verdict} confidence={self.confidence}>"

    @property
    def pending_recommendations(self) -> list[ReviewRecommendation]:
        """Shortcut — filter in Python, no extra query (relies on selectin load)."""
        return [r for r in self.recommendations if r.status == RecommendationStatus.PENDING]


# ---------------------------------------------------------------------------
# ReviewRecommendation (AI-suggested status updates, PENDING until user acts)
# ---------------------------------------------------------------------------


class ReviewRecommendation(Base):
    """Một đề xuất cụ thể từ AI review để cập nhật assumption hoặc catalyst.

    Lifecycle:
        PENDING  → user xem và confirm/reject
        ACCEPTED → ThesisService.apply_recommendation() đã apply
        REJECTED → user bỏ qua
        EXPIRED  → review mới hơn tạo recommendation supersede cái này

    target_type + target_id trỏ đến Assumption.id hoặc Catalyst.id.
    recommended_status là string (không phải enum) để linh hoạt với
    cả AssumptionStatus lẫn CatalystStatus.
    """

    __tablename__ = "review_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("thesis_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_type: Mapped[str] = mapped_column(
        SAEnum(RecommendationTargetType, values_callable=_enum_values),
        nullable=False,
    )
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_description: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Snapshot mô tả tại thời điểm review, để hiển thị cho user"
    )
    recommended_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="valid | invalid | uncertain | triggered | expired | cancelled",
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RecommendationStatus] = mapped_column(
        SAEnum(RecommendationStatus, values_callable=_enum_values),
        nullable=False,
        default=RecommendationStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    acted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="Khi user ACCEPTED hoặc REJECTED",
    )

    # Relationship
    review: Mapped[ThesisReview] = relationship(back_populates="recommendations")

    def __repr__(self) -> str:
        return (
            f"<ReviewRecommendation id={self.id} "
            f"target={self.target_type}:{self.target_id} "
            f"status={self.status}>"
        )


# ---------------------------------------------------------------------------
# ThesisSnapshot (point-in-time performance record)
# ---------------------------------------------------------------------------


class ThesisSnapshot(Base):
    __tablename__ = "thesis_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thesis_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("theses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    price_at_snapshot: Mapped[float] = mapped_column(Float, nullable=False)
    pnl_pct: Mapped[float | None] = mapped_column(Float)  # vs entry_price
    score_at_snapshot: Mapped[float | None] = mapped_column(Float)
    snapshotted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationship
    thesis: Mapped[Thesis] = relationship(back_populates="snapshots")
