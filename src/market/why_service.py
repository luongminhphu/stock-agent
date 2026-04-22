"""WhyService — orchestrates data collection for price movement explanation.
Owner: market segment.
Caller: bot/commands/why.py (adapter only).
"""

from __future__ import annotations

from src.ai.agents.why import WhyAgent
from src.ai.schemas import WhyOutput
from src.market.ohlcv_service import OHLCVService, OHLCVServiceNotConfiguredError
from src.market.quote_service import QuoteService
from src.market.registry import SymbolNotFoundError, registry
from src.platform.logging import get_logger

logger = get_logger(__name__)
_OHLCV_CANDLES = 5  # 5 phiên gần nhất đủ để thấy context
_MIN_CHANGE_PCT = 0.01  # bỏ qua nếu biến động quá nhỏ


class WhyService:
    def __init__(
        self,
        quote_service: QuoteService,
        ohlcv_service: OHLCVService,
        why_agent: WhyAgent,
    ) -> None:
        self._qs = quote_service
        self._ohlcv = ohlcv_service
        self._agent = why_agent

    async def explain(self, ticker: str) -> WhyOutput:
        ticker = ticker.upper()

        # 1. Registry lookup — inject tên + ngành cho AI
        try:
            info = registry.resolve(ticker)
            company_name = info.name
            sector = info.sector
        except SymbolNotFoundError:
            company_name = ticker
            sector = "Unknown"
            logger.warning("why_service.ticker_not_in_registry", ticker=ticker)

        # 2. Quote hiện tại
        try:
            quote = await self._qs.get_quote(ticker)
            change_pct = quote.change_pct
            price = quote.price
            volume = getattr(quote, "volume", None)
        except Exception as exc:
            logger.warning("why_service.quote_failed", ticker=ticker, error=str(exc))
            raise ValueError(f"Không lấy được giá hiện tại cho {ticker}: {exc}") from exc

        # 3. Guard — không phân tích khi không có biến động
        if abs(change_pct) < _MIN_CHANGE_PCT:
            raise ValueError(
                f"{ticker} không có biến động đáng kể hôm nay ({change_pct:+.2f}%). "
                "Thử lại khi mã có biến động rõ hơn."
            )

        # 4. OHLCV 5 phiên — build context string
        ohlcv_summary = await self._build_ohlcv_summary(ticker)

        return await self._agent.explain(
            ticker=ticker,
            company_name=company_name,
            sector=sector,
            change_pct=change_pct,
            price=price,
            volume=volume,
            ohlcv_summary=ohlcv_summary,
        )

    async def _build_ohlcv_summary(self, ticker: str) -> str:
        try:
            # get_latest_candles() — đúng method name theo OHLCVService API
            bars = await self._ohlcv.get_latest_candles(ticker, n=_OHLCV_CANDLES)
            if not bars:
                return ""
            lines = ["Ngày       | Mở       | Cao      | Thấp     | Đóng     | Volume"]
            for b in bars:
                lines.append(
                    f"{b.date} | {b.open:>8,.0f} | {b.high:>8,.0f} | "
                    f"{b.low:>8,.0f} | {b.close:>8,.0f} | {b.volume:>10,}"
                )
            return "\n".join(lines)
        except OHLCVServiceNotConfiguredError:
            # Wave 1: adapter chưa có — fallback silent, AI chạy với data_quality note
            logger.info("why_service.ohlcv_not_configured", ticker=ticker)
            return ""
        except Exception as exc:
            logger.warning("why_service.ohlcv_failed", ticker=ticker, error=str(exc))
            return ""  # fallback silent — AI vẫn chạy, ghi vào data_quality
