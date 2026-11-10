"""Đối soát giá mở cửa / đóng cửa khi chốt phiên — để giá trong app khớp với CTCK.

Owner: market segment.

Vấn đề:
    Mọi bảng giá CTCK hiển thị cùng một giá mở cửa (khớp ATO) và giá đóng cửa
    (khớp ATC / lần khớp cuối) do sở công bố. App trước đây lấy giá đóng cửa từ
    *một* lần gọi bảng giá VCI lúc 14:50: nếu VCI trả trễ, sai trường, hoặc chuỗi
    fallback rơi sang nguồn khác, giá lưu vào P&L/outcome sẽ lệch với CTCK.

Cơ chế (ưu tiên từ trên xuống, mỗi mã chỉ lấy dữ liệu từ MỘT nguồn thắng):
    1. Nến ngày chính thức của phiên (VCI OHLC D1) — nguồn mà biểu đồ của CTCK
       dùng. Có nến cho đúng ngày phiên → open/high/low/close lấy từ nến.
    2. Không có nến → so bảng giá nguồn chính với nguồn đối chứng độc lập (KBS).
       Hai nguồn trùng nhau (lệch < ½ bước giá) → đồng thuận. Lệch → giữ nguồn
       chính nếu hợp lệ, gắn cờ để theo dõi.
    3. Chỉ có nguồn chính → dùng nguồn chính.
    Sau đó mọi giá được chuẩn hoá về lưới bước giá của sàn và kiểm tra biên
    trần/sàn. Mọi bất thường được ghi vào `PriceCheck.flags` (không raise).

Giá trị trả về là Quote đã chốt: price = close = giá đóng cửa chính thức,
change/change_pct tính lại theo tham chiếu.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from src.market.ohlcv_service import Candle
from src.market.price_rules import is_on_tick, prices_match, snap_to_tick, tick_size
from src.market.quote_service import MarketDataAdapter, Quote
from src.platform.logging import get_logger

logger = get_logger(__name__)

_CANDLE_LOOKBACK_DAYS = 7
_MAX_CONCURRENT_CANDLES = 5

# Nguồn thắng cho từng mã
SOURCE_CANDLE = "candle"
SOURCE_CONSENSUS = "consensus"
SOURCE_PRIMARY = "primary"
SOURCE_PEER = "peer"

# Cờ bất thường
FLAG_CLOSE_VS_CANDLE = "close_mismatch_candle"  # bảng giá nguồn chính ≠ nến chính thức
FLAG_OPEN_VS_CANDLE = "open_mismatch_candle"
FLAG_CLOSE_VS_PEER = "close_mismatch_peer"  # nguồn chính ≠ nguồn đối chứng
FLAG_OPEN_VS_PEER = "open_mismatch_peer"
FLAG_OFF_TICK = "off_tick"  # giá không nằm trên lưới bước giá → đã làm tròn
FLAG_OUT_OF_BAND = "out_of_band"  # giá ngoài biên trần/sàn


@dataclass(frozen=True)
class PriceCheck:
    ticker: str
    exchange: str
    source: str
    flags: tuple[str, ...] = ()
    primary_close: float | None = None
    official_close: float | None = None


def exchange_for(ticker: str) -> str:
    """Sàn của mã theo registry; mã chưa biết → '' (price_rules dùng luật an toàn nhất)."""
    from src.market.registry import registry  # noqa: PLC0415 — tránh vòng import khi load

    info = registry.get(ticker)
    return info.exchange.value if info is not None else ""


def _within_band(price: float, quote: Quote, exchange: str) -> bool:
    if quote.ceiling <= 0 or quote.floor <= 0:
        return True
    half = tick_size(price, exchange) / 2
    return quote.floor - half <= price <= quote.ceiling + half


def reconcile_quote(
    quote: Quote,
    exchange: str,
    candle: Candle | None = None,
    peer: Quote | None = None,
) -> tuple[Quote, PriceCheck]:
    """Hàm thuần: chọn giá mở/đóng cửa chính thức cho một mã đã có khớp lệnh."""
    flags: list[str] = []
    open_, high, low, close = quote.open, quote.high, quote.low, quote.price
    source = SOURCE_PRIMARY

    if candle is not None and candle.close > 0:
        source = SOURCE_CANDLE
        if not prices_match(candle.close, quote.price, exchange):
            flags.append(FLAG_CLOSE_VS_CANDLE)
        if candle.open > 0 and not prices_match(candle.open, quote.open, exchange):
            flags.append(FLAG_OPEN_VS_CANDLE)
        open_ = candle.open or open_
        close = candle.close
        high = max(candle.high, close) if candle.high > 0 else high
        low = min(candle.low, close) if candle.low > 0 else low
    elif peer is not None and peer.volume > 0 and peer.price > 0:
        if prices_match(peer.price, quote.price, exchange):
            source = SOURCE_CONSENSUS
        else:
            flags.append(FLAG_CLOSE_VS_PEER)
            primary_ok = is_on_tick(quote.price, exchange) and _within_band(
                quote.price, quote, exchange
            )
            peer_ok = is_on_tick(peer.price, exchange) and _within_band(peer.price, quote, exchange)
            if not primary_ok and peer_ok:
                # Nguồn chính trả giá không hợp lệ (lệch lưới/biên) → tin nguồn đối chứng
                source = SOURCE_PEER
                open_, high, low, close = peer.open, peer.high, peer.low, peer.price
        if peer.open > 0 and not prices_match(peer.open, quote.open, exchange):
            flags.append(FLAG_OPEN_VS_PEER)

    snapped_close = snap_to_tick(close, exchange)
    snapped_open = snap_to_tick(open_, exchange)
    if snapped_close != close or snapped_open != open_:
        flags.append(FLAG_OFF_TICK)
    close, open_ = snapped_close, snapped_open
    high = max(snap_to_tick(high, exchange), close, open_)
    low = min(snap_to_tick(low, exchange), close, open_)
    if not _within_band(close, quote, exchange):
        flags.append(FLAG_OUT_OF_BAND)

    change = close - quote.ref_price
    change_pct = change / quote.ref_price * 100 if quote.ref_price else 0.0
    settled = replace(
        quote,
        price=close,
        close=close,
        open=open_,
        high=high,
        low=low,
        change=change,
        change_pct=round(change_pct, 2),
    )
    check = PriceCheck(
        ticker=quote.ticker,
        exchange=exchange,
        source=source,
        flags=tuple(flags),
        primary_close=quote.price,
        official_close=close,
    )
    return settled, check


class SessionPriceReconciler:
    """Điều phối đối soát cho cả lô mã khi chốt phiên.

    Args:
        ohlcv_service:     nguồn nến ngày chính thức (OHLCVService); None → bỏ bước 1.
        reference_adapter: nguồn bảng giá đối chứng độc lập (KBSAdapter); None → bỏ bước 2.
        exchange_of:       ticker → sàn; mặc định tra registry.
    """

    def __init__(
        self,
        ohlcv_service: Any = None,
        reference_adapter: MarketDataAdapter | None = None,
        exchange_of: Callable[[str], str] = exchange_for,
    ) -> None:
        self._ohlcv = ohlcv_service
        self._peer = reference_adapter
        self._exchange_of = exchange_of
        self._sem = asyncio.Semaphore(_MAX_CONCURRENT_CANDLES)

    async def _candle_for(self, ticker: str, session_date: date) -> Candle | None:
        if self._ohlcv is None:
            return None
        async with self._sem:
            try:
                candles = await self._ohlcv.get_candles(
                    ticker, session_date - timedelta(days=_CANDLE_LOOKBACK_DAYS), session_date
                )
            except Exception as exc:  # noqa: BLE001 — nến chỉ là nguồn đối soát
                logger.warning("price_reconcile.candle_failed", ticker=ticker, error=str(exc))
                return None
        for c in reversed(candles or []):
            if c.date == session_date:
                return c
        return None

    async def _peer_quotes(self, tickers: list[str]) -> dict[str, Quote]:
        if self._peer is None or not tickers:
            return {}
        try:
            quotes = await self._peer.fetch_bulk_quotes(tickers)
        except Exception as exc:  # noqa: BLE001
            logger.warning("price_reconcile.peer_failed", count=len(tickers), error=str(exc))
            return {}
        return {q.ticker.upper(): q for q in quotes}

    async def reconcile(
        self, quotes: list[Quote], session_date: date
    ) -> tuple[list[Quote], list[PriceCheck]]:
        if not quotes:
            return [], []
        tickers = [q.ticker.upper() for q in quotes]
        candles, peers = await asyncio.gather(
            asyncio.gather(*[self._candle_for(t, session_date) for t in tickers]),
            self._peer_quotes(tickers),
        )
        settled: list[Quote] = []
        checks: list[PriceCheck] = []
        for q, candle in zip(quotes, candles, strict=True):
            t = q.ticker.upper()
            out, check = reconcile_quote(q, self._exchange_of(t), candle, peers.get(t))
            settled.append(out)
            checks.append(check)
            if check.flags:
                logger.warning(
                    "price_reconcile.flagged",
                    ticker=t,
                    source=check.source,
                    flags=list(check.flags),
                    primary_close=check.primary_close,
                    official_close=check.official_close,
                )
        logger.info(
            "price_reconcile.done",
            session_date=session_date.isoformat(),
            total=len(checks),
            from_candle=sum(1 for c in checks if c.source == SOURCE_CANDLE),
            consensus=sum(1 for c in checks if c.source == SOURCE_CONSENSUS),
            flagged=sum(1 for c in checks if c.flags),
        )
        return settled, checks
