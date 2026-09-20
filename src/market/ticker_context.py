"""TickerContext — lớp market data đã tinh chế, dùng chung cho mọi consumer.

Owner: market.

Vấn đề giải quyết (Wave B2): watchlist scan, thesis stop-breach/drift/pretrade,
opportunity screen, briefing... mỗi bên tự kéo `Quote` rồi tự suy diễn; không bên
nào có MA/RSI/ATR/volume ratio nhất quán, và `Quote` không có `volume_ratio` nên
alert khối lượng ở watchlist chưa bao giờ trigger được.

`TickerContextService.get_many(tickers)` = 1 bulk quote + OHLCV (đã cache theo phiên
trong OHLCVService) → indicators từ `src.market.indicators` → `TickerContext`.

Contract ổn định, downstream (ai/thesis/watchlist/briefing/readmodel) chỉ đọc.
Fallback: OHLCV lỗi → phần indicator = None, `source_quality="fallback"`, quote vẫn
có; consumer cũ dựa trên `quote` chạy như trước.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from src.market import indicators
from src.market.ohlcv_service import Candle, Interval, OHLCVServiceNotConfiguredError
from src.market.quote_service import Quote
from src.platform.logging import get_logger

logger = get_logger(__name__)

_ICT = ZoneInfo("Asia/Ho_Chi_Minh")

TrendState = Literal["UP", "DOWN", "SIDEWAYS", "UNKNOWN"]
SourceQuality = Literal["live", "stale", "fallback"]

# ~52 tuần lịch; OHLCVService cache theo key (ticker, from, to) nên trong 1 ngày
# mọi consumer dùng chung 1 lần fetch / ticker.
DEFAULT_LOOKBACK_DAYS = 365
_MIN_BARS_FOR_INDICATORS = 30
_CONCURRENCY = 8


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickerContext:
    """Market snapshot đã tinh chế cho một mã. Mọi field indicator có thể None."""

    ticker: str
    as_of: datetime
    quote: Quote

    ma20: float | None = None
    ma50: float | None = None
    rsi14: float | None = None
    atr14: float | None = None
    vol_ratio_20: float | None = None  # volume phiên gần nhất / TB 20 phiên trước
    hi_52w: float | None = None
    lo_52w: float | None = None
    bars: int = 0  # số candle D1 dùng để tính

    trend_state: TrendState = "UNKNOWN"
    source_quality: SourceQuality = "fallback"

    # --- tiện ích đọc, không chứa rule nghiệp vụ của segment khác -------------
    @property
    def price(self) -> float:
        return self.quote.price

    @property
    def change_pct(self) -> float:
        return self.quote.change_pct

    @property
    def has_indicators(self) -> bool:
        return self.ma20 is not None

    @property
    def dist_to_ma20_pct(self) -> float | None:
        if self.ma20 is None or self.ma20 == 0:
            return None
        return (self.quote.price / self.ma20 - 1.0) * 100.0

    @property
    def dist_to_hi_52w_pct(self) -> float | None:
        if self.hi_52w is None or self.hi_52w == 0:
            return None
        return (self.quote.price / self.hi_52w - 1.0) * 100.0

    def format_for_prompt(self) -> str:
        """Dòng tóm tắt gọn cho prompt AI — không bịa số khi thiếu dữ liệu."""

        def f(v: float | None, fmt: str = ",.0f") -> str:
            return "n/a" if v is None else format(v, fmt)

        parts = [
            f"{self.ticker} {self.quote.price:,.0f} ({self.quote.change_pct:+.2f}%)",
            f"MA20 {f(self.ma20)} · MA50 {f(self.ma50)}",
            f"RSI14 {f(self.rsi14, '.1f')} · ATR14 {f(self.atr14)}",
            f"Vol/TB20 {f(self.vol_ratio_20, '.2f')}x",
            f"52w {f(self.lo_52w)}–{f(self.hi_52w)}",
            f"trend {self.trend_state} · data {self.source_quality}",
        ]
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# Pure builders (test được không cần I/O)
# ---------------------------------------------------------------------------


def classify_trend(price: float, ma20: float | None, ma50: float | None) -> TrendState:
    """Quy tắc xu hướng tối giản, deterministic: price vs MA20 vs MA50."""
    if ma20 is None or ma50 is None:
        return "UNKNOWN"
    if price > ma20 > ma50:
        return "UP"
    if price < ma20 < ma50:
        return "DOWN"
    return "SIDEWAYS"


def quote_quality(quote: Quote, now: datetime) -> SourceQuality:
    """`live` nếu quote thuộc ngày giao dịch hiện tại (ICT), ngược lại `stale`."""
    ts = quote.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return "live" if ts.astimezone(_ICT).date() == now.astimezone(_ICT).date() else "stale"


def build_context(
    quote: Quote,
    candles: list[Candle] | None,
    *,
    now: datetime | None = None,
) -> TickerContext:
    """Gộp quote + candles thành TickerContext. `candles=None` → fallback (chỉ quote)."""
    now = now or datetime.now(UTC)
    base = TickerContext(
        ticker=quote.ticker,
        as_of=now,
        quote=quote,
        source_quality="fallback",
    )
    if not candles or len(candles) < _MIN_BARS_FOR_INDICATORS:
        return base

    candles = sorted(candles, key=lambda c: c.date)
    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    volumes = [float(c.volume) for c in candles]

    # Candle hôm nay (nếu có) chưa chốt — dùng quote làm giá hiện tại,
    # còn 52w/vol ratio dựa trên chuỗi candle đã có.
    ma20 = indicators.sma(closes, 20)
    ma50 = indicators.sma(closes, 50)
    rsi14 = indicators.rsi(closes, 14) if len(closes) >= 29 else None
    atr14_raw = indicators.atr(highs, lows, closes, 14)
    atr14 = atr14_raw if atr14_raw > 0 else None
    hl_high = indicators.high_low(highs, 250)
    hl_low = indicators.high_low(lows, 250)
    hi_52w = hl_high[0] if hl_high else None
    lo_52w = hl_low[1] if hl_low else None

    return TickerContext(
        ticker=quote.ticker,
        as_of=now,
        quote=quote,
        ma20=ma20,
        ma50=ma50,
        rsi14=rsi14,
        atr14=atr14,
        vol_ratio_20=indicators.volume_ratio(volumes, 20),
        hi_52w=hi_52w,
        lo_52w=lo_52w,
        bars=len(candles),
        trend_state=classify_trend(quote.price, ma20, ma50),
        source_quality=quote_quality(quote, now),
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class _QuoteSource(Protocol):
    async def get_quote(self, ticker: str) -> Quote: ...
    async def get_bulk_quotes(self, tickers: list[str]) -> list[Quote]: ...


class _CandleSource(Protocol):
    async def get_candles(
        self, ticker: str, from_date: date, to_date: date, interval: Interval = Interval.D1
    ) -> list[Candle]: ...


class TickerContextService:
    """Điểm vào duy nhất để lấy market context đã tinh chế cho N mã.

    Args:
        quote_service:  QuoteService (đã có cache 3s/15p + last-known).
        ohlcv_service:  OHLCVService (cache theo phiên, Wave B1). None → luôn fallback.
        lookback_days:  số ngày lịch kéo candle D1. Mặc định 365 (đủ 52w).
    """

    def __init__(
        self,
        quote_service: _QuoteSource,
        ohlcv_service: _CandleSource | None = None,
        *,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        self._quotes = quote_service
        self._ohlcv = ohlcv_service
        self._lookback = lookback_days
        self._sem = asyncio.Semaphore(_CONCURRENCY)

    async def get(self, ticker: str) -> TickerContext:
        result = await self.get_many([ticker])
        ctx = result.get(ticker.upper())
        if ctx is None:
            raise LookupError(f"No quote available for {ticker}")
        return ctx

    async def get_many(self, tickers: list[str]) -> dict[str, TickerContext]:
        """Trả map ticker → TickerContext. Mã không lấy được quote sẽ vắng mặt trong map."""
        syms = sorted({t.upper() for t in tickers if t})
        if not syms:
            return {}

        quotes = await self._fetch_quotes(syms)
        if not quotes:
            return {}

        now = datetime.now(UTC)
        candles = await asyncio.gather(*(self._fetch_candles(s, now) for s in quotes))
        return {
            sym: build_context(quotes[sym], cnd, now=now)
            for sym, cnd in zip(quotes, candles, strict=True)
        }

    # -- internals ------------------------------------------------------------

    async def _fetch_quotes(self, syms: list[str]) -> dict[str, Quote]:
        try:
            bulk = await self._quotes.get_bulk_quotes(syms)
            found = {q.ticker.upper(): q for q in bulk}
        except Exception as exc:
            logger.warning("ticker_context.bulk_quote_failed", tickers=syms, error=str(exc))
            found = {}

        missing = [s for s in syms if s not in found]
        for sym in missing:  # per-ticker fallback, best-effort
            try:
                found[sym] = await self._quotes.get_quote(sym)
            except Exception as exc:
                logger.debug("ticker_context.quote_missing", ticker=sym, error=str(exc))
        return {s: found[s] for s in syms if s in found}

    async def _fetch_candles(self, sym: str, now: datetime) -> list[Candle] | None:
        if self._ohlcv is None:
            return None
        today = now.astimezone(_ICT).date()
        async with self._sem:
            try:
                return await self._ohlcv.get_candles(
                    sym, today - timedelta(days=self._lookback), today, Interval.D1
                )
            except OHLCVServiceNotConfiguredError:
                return None
            except Exception as exc:
                logger.warning("ticker_context.ohlcv_failed", ticker=sym, error=str(exc))
                return None


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "SourceQuality",
    "TickerContext",
    "TickerContextService",
    "TrendState",
    "build_context",
    "classify_trend",
    "quote_quality",
]
