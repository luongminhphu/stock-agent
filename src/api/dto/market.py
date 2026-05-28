"""Market DTOs.

Owner: api segment.
All response models are Pydantic — no SQLAlchemy objects cross this boundary.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class SymbolInfoResponse(BaseModel):
    ticker: str
    name: str
    exchange: str
    sector: str


class QuoteResponse(BaseModel):
    ticker: str
    name: str
    # Price data
    price: float | None
    change: float | None
    change_pct: float | None
    volume: int | None
    value: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    ref_price: float | None = None
    ceiling: float | None = None
    floor: float | None = None
    # Derived flags
    is_ceiling: bool | None = None
    is_floor: bool | None = None
    # Human-readable strings
    formatted_price: str | None = None
    formatted_change: str | None = None
    # Metadata
    timestamp: datetime | None = None
    note: str | None = None


class CandleResponse(BaseModel):
    """Single OHLCV candle — minimal shape for FE price chart."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class BreadthResponse(BaseModel):
    """Market breadth snapshot for a given exchange scope.

    Returned by GET /market/breadth.
    """

    exchange: str           # "HOSE" | "HNX" | "UPCOM" | "ALL"
    advance: int            # number of tickers with change > 0
    decline: int            # number of tickers with change < 0
    unchanged: int          # number of tickers with change == 0
    ceiling: int            # tickers at ceiling price (trần)
    floor: int              # tickers at floor price (sàn)
    total: int              # total tickers with valid quotes
    advance_pct: float      # advance / total * 100
    decline_pct: float      # decline / total * 100
    unchanged_pct: float    # unchanged / total * 100
