"""ORM models thuộc market segment (Wave E1a — trả bảng về đúng owner).

Tables:
    market_quote_cache  — QuoteService warm-load (writer: market.quote_service)
    market_daily_candles — lịch sử nến ngày (writer: market.candle_store)
    market_candle_coverage — khoảng ngày đã lưu đủ cho từng mã (writer: market.candle_store)

Tên bảng không đổi → không cần migration. readmodel chỉ query (projection).
Upsert qua ``platform.db.upsert_rows``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.db import Base


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
    close: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Wave C1: giá đóng cửa chính thức (ATC) do capture_eod_close ghi; NULL trong phiên",
    )
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


class DailyCandle(Base):
    """Nến ngày đã chốt phiên — nguồn lịch sử cho base-rate/RRG/ticker context.

    Lưu để mỗi ngày chỉ tải phần còn thiếu thay vì tải lại 10 năm mỗi lần phân tích
    (giảm mạnh số request tới API của CTCK). Giá là giá điều chỉnh theo nguồn.
    """

    __tablename__ = "market_daily_candles"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class CandleCoverage(Base):
    """Khoảng ngày [first_date, last_date] đã tải đủ cho một mã."""

    __tablename__ = "market_candle_coverage"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    first_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_date: Mapped[date] = mapped_column(Date, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
