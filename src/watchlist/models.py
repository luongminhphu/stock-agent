"""SQLAlchemy ORM models for the watchlist segment.

Owner: watchlist segment only.
Other segments access watchlist data through WatchlistService,
never by importing these models directly.
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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.platform.db import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AlertConditionType(str, enum.Enum):
    PRICE_ABOVE = "price_above"       # price >= threshold
    PRICE_BELOW = "price_below"       # price <= threshold
    CHANGE_PCT_UP = "change_pct_up"   # daily change% >= threshold
    CHANGE_PCT_DOWN = "change_pct_down"  # daily change% <= -threshold
    VOLUME_SPIKE = "volume_spike"     # volume >= threshold * avg_volume


class AlertStatus(str, enum.Enum):
    ACTIVE = "active"
    TRIGGERED = "triggered"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class ReminderFrequency(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    ON_SIGNAL = "on_signal"  # only when scan detects a signal


# ---------------------------------------------------------------------------
# WatchlistItem
# ---------------------------------------------------------------------------


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("user_id", "ticker", name="uq_watchlist_user_ticker"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    # Optional link to a thesis — stores ID only, no FK to keep segments decoupled
    thesis_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # Priority for ordering in UI (lower = higher priority)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    alerts: Mapped[list[Alert]] = relationship(
        back_populates="watchlist_item", cascade="all, delete-orphan"
    )
    reminder: Mapped[Reminder | None] = relationship(
        back_populates="watchlist_item",
        cascade="all, delete-orphan",
        uselist=False,
    )

    def __repr__(self) -> str:
        return f"<WatchlistItem user={self.user_id} ticker={self.ticker}>"


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    watchlist_item_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("watchlist_items.id", ondelete="CASCADE"), index=True
    )

    condition_type: Mapped[AlertConditionType] = mapped_column(
        SAEnum(AlertConditionType), nullable=False
    )
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[AlertStatus] = mapped_column(
        SAEnum(AlertStatus), nullable=False, default=AlertStatus.ACTIVE
    )

    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triggered_price: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationship
    watchlist_item: Mapped[WatchlistItem | None] = relationship(back_populates="alerts")

    def __repr__(self) -> str:
        return (
            f"<Alert ticker={self.ticker} "
            f"condition={self.condition_type} threshold={self.threshold} "
            f"status={self.status}>"
        )

    # ------------------------------------------------------------------
    # Domain helpers
    # ------------------------------------------------------------------

    def is_triggered_by(self, current_price: float, change_pct: float, volume_ratio: float) -> bool:
        """Check if this alert should fire given current market data.

        Does NOT mutate state — caller (ScanService) decides to trigger.
        """
        if self.status != AlertStatus.ACTIVE:
            return False
        match self.condition_type:
            case AlertConditionType.PRICE_ABOVE:
                return current_price >= self.threshold
            case AlertConditionType.PRICE_BELOW:
                return current_price <= self.threshold
            case AlertConditionType.CHANGE_PCT_UP:
                return change_pct >= self.threshold
            case AlertConditionType.CHANGE_PCT_DOWN:
                return change_pct <= -self.threshold
            case AlertConditionType.VOLUME_SPIKE:
                return volume_ratio >= self.threshold
            case _:
                return False


# ---------------------------------------------------------------------------
# Reminder
# ---------------------------------------------------------------------------


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    watchlist_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("watchlist_items.id", ondelete="CASCADE"), nullable=False
    )
    frequency: Mapped[ReminderFrequency] = mapped_column(
        SAEnum(ReminderFrequency), nullable=False, default=ReminderFrequency.ON_SIGNAL
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationship
    watchlist_item: Mapped[WatchlistItem] = relationship(back_populates="reminder")

    def __repr__(self) -> str:
        return f"<Reminder freq={self.frequency} enabled={self.enabled}>"
