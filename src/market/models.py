"""ORM models thuộc market segment (Wave E1a — trả bảng về đúng owner).

Tables:
    market_quote_cache  — QuoteService warm-load (writer: market.quote_service)
    trend_snapshots     — TrendSnapshotStore (writer: market.trend_shift_detector)
    trend_predictions   — TrendPredictionStore (writer: ai.trend_engine_listener)

Tên bảng không đổi → không cần migration. readmodel chỉ query (projection).
Upsert qua ``platform.db.upsert_rows``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.db import Base


class TrendSnapshot(Base):
    """Persisted TechnicalSignalBundle per symbol — market.TrendSnapshotStore.

    Survives bot restarts so TrendShiftDetector always has a baseline to compare
    against instead of treating every post-restart cycle as cold start.

    PK: symbol (one row per symbol, upserted on every save()).
    """

    __tablename__ = "trend_snapshots"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    bundle_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="JSON: TechnicalSignalBundle.model_dump()",
    )
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class TrendPrediction(Base):
    """Persisted TrendPrediction per symbol — market.TrendPredictionStore.

    Allows briefing, bot /trend, and API /trend to serve the last known
    prediction after a restart without re-running the AI engine.

    expires_at: prediction is treated as absent after this timestamp (4h TTL).
    """

    __tablename__ = "trend_predictions"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    verdict: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="STRONG_BUY | BUY | WATCH | HOLD | REDUCE | STRONG_SELL",
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reasoning_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="JSON: full TrendPrediction.model_dump() for warm restore",
    )
    predicted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="predicted_at + 4h — filter on load to skip stale predictions",
    )


class MarketQuoteCache(Base):
    """Persisted last-known quote per ticker — QuoteService warm-load on restart.

    Owner: market segment (Wave E1a — chuyển từ readmodel; QuoteService ghi, readmodel chỉ đọc).
    Strategy: upsert — 1 row per ticker, always the most recent successful fetch.

    Used by QuoteService to warm _last_known from DB on startup so the dashboard
    does not show N/A after a process restart outside trading hours.
    """

    __tablename__ = "market_quote_cache"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    change: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    change_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    volume: Mapped[int] = mapped_column(
        # BigInt để tránh overflow với CP ngàn tỷ
        BigInteger,
        nullable=False,
        default=0,
    )
    value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    open: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    high: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    low: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ref_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ceiling: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    floor: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quote_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Timestamp of the original quote from adapter",
    )
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        comment="Last time this row was upserted",
    )
