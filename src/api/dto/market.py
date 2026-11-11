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


class TickerContextResponse(BaseModel):
    """Wave U2a: market.TickerContext cho FE — cùng bối cảnh kỹ thuật AI đang đọc.

    Mọi indicator có thể None (thiếu OHLCV / market đóng) — FE hiện "—".
    """

    ticker: str
    name: str
    as_of: datetime | None = None
    # Quote
    price: float | None
    change: float | None
    change_pct: float | None
    volume: int | None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    ref_price: float | None = None
    ceiling: float | None = None
    floor: float | None = None
    is_ceiling: bool | None = None
    is_floor: bool | None = None
    formatted_price: str | None = None
    # Indicators (D1)
    ma20: float | None = None
    ma50: float | None = None
    rsi14: float | None = None
    atr14: float | None = None
    vol_ratio_20: float | None = None
    hi_52w: float | None = None
    lo_52w: float | None = None
    dist_to_ma20_pct: float | None = None
    dist_to_hi_52w_pct: float | None = None
    bars: int = 0
    # Đánh giá
    trend_state: str = "UNKNOWN"
    source_quality: str = "fallback"


class CandleResponse(BaseModel):
    """Single OHLCV candle — minimal shape for FE price chart."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class BaseRateOutcomeResponse(BaseModel):
    """Kết quả lịch sử sau `horizon` phiên của các lần mã ở cùng trạng thái."""

    horizon: int
    episodes: int  # số đợt độc lập (không chồng cửa sổ)
    win_prob: float  # xác suất lãi vượt chi phí, đã co về mức nền của mã
    prob_low: float  # khoảng ~80%
    prob_high: float
    raw_win_rate: float | None
    base_win_rate: float  # mức nền: mọi ngày trong lịch sử mã
    edge: float  # win_prob - base_win_rate
    median_return: float | None
    p10_return: float | None
    p90_return: float | None
    base_median_return: float | None
    avg_max_drawdown: float | None


class BaseRateAnalogResponse(BaseModel):
    entry_date: date
    entry_close: float
    returns: dict[int, float]
    max_drawdown: dict[int, float]


class BaseRateResponse(BaseModel):
    """GET /market/baserate/{ticker} — tiền lệ trong lịch sử chính mã, không dự báo."""

    ticker: str
    as_of: date
    state: dict[str, str]
    state_labels: dict[str, str]  # mô tả tiếng Việt của từng đặc trưng
    matched_on: list[str]
    dropped: list[str]
    matching_days: int
    suggestion: str  # NGHIENG_MUA | NGHIENG_TRANH | TRUNG_TINH | KHONG_DU_TIEN_LE
    confidence: str  # khong_du | thap | trung_binh | cao
    outcomes: list[BaseRateOutcomeResponse]
    analogs: list[BaseRateAnalogResponse]
    history_from: date | None
    notes: list[str]
