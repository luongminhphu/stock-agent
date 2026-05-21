"""TrendContextFetcher — market segment.

Gom news_snippets + market_context từ các adapter sẵn có,
render thành string để TrendEngine inject vào TrendReasoningAgent.

Owner: market segment.
Consumers: TrendEngine (internal), API route (optional direct use).

Design:
- Chạy news fetch + regime fetch song song (asyncio.gather)
- Timeout 10s total — không block TrendEngine quá lâu
- Luôn trả (str, str), không raise
- Không biết về AI schema, không biết về bot
"""
from __future__ import annotations

import asyncio

from src.market.news_adapter import TCBSNewsAdapter
from src.market.market_regime import MarketRegimeService
from src.platform.logging import get_logger

logger = get_logger(__name__)

_MAX_NEWS = 5
_FETCH_TIMEOUT = 10.0
_EMPTY_NEWS = "Không có tin tức gần đây"
_EMPTY_MARKET = "Không có dữ liệu thị trường tổng thể"


class TrendContextFetcher:
    """Fetch và render context cho TrendEngine.

    Usage::

        fetcher = TrendContextFetcher(news_adapter, regime_service)
        news_str, market_str = await fetcher.fetch(symbol="VCB")
        prediction = await trend_engine.predict(symbol, news_str, market_str)
    """

    def __init__(
        self,
        news_adapter: TCBSNewsAdapter,
        regime_service: MarketRegimeService,
    ) -> None:
        self._news = news_adapter
        self._regime = regime_service

    async def fetch(self, symbol: str) -> tuple[str, str]:
        """Trả (news_snippets_str, market_context_str).

        Chạy song song, timeout 10s total, fallback về empty strings.
        """
        try:
            news_items, regime = await asyncio.wait_for(
                asyncio.gather(
                    self._news.get_news(symbol, limit=_MAX_NEWS),
                    self._regime.get_regime(),
                    return_exceptions=False,
                ),
                timeout=_FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("trend_context_fetcher.timeout", extra={"symbol": symbol})
            return _EMPTY_NEWS, _EMPTY_MARKET
        except Exception as exc:
            logger.warning(
                "trend_context_fetcher.error",
                extra={"symbol": symbol, "error": str(exc)},
            )
            return _EMPTY_NEWS, _EMPTY_MARKET

        news_str = _render_news(news_items)
        market_str = regime.format_for_prompt()
        return news_str, market_str


def _render_news(items: list) -> str:
    if not items:
        return _EMPTY_NEWS
    return "\n".join(item.format_for_prompt() for item in items)
