"""News Adapter — market segment.

Fetch tin tức liên quan đến symbol từ TCBS (public endpoint, no auth).
Owner: market segment.
Contract: NewsItem DTO — stable, dùng bởi TrendContextFetcher.

Caching:
- TCBSNewsAdapter dùng AsyncTTLCache với TTL mặc định 15 phút.
- Stampede protection: nhiều coroutine cùng hỏi 1 symbol → chỉ 1 HTTP call.
- Fallback: nếu TCBS fail → trả list rỗng, không raise.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from src.platform.logging import get_logger
from src.platform.ttl_cache import AsyncTTLCache

logger = get_logger(__name__)

_TCBS_NEWS_URL = "https://apipubaws.tcbs.com.vn/tcanalysis/v1/ticker/{symbol}/activity-news"
_TCBS_NEWS_PARAMS: dict[str, Any] = {"page": 0, "size": 5}
_TIMEOUT = 8.0
_NEWS_TTL = 15 * 60  # 15 phút


@dataclass(frozen=True)
class NewsItem:
    title: str
    published_at: datetime
    source: str
    snippet: str = ""

    def format_for_prompt(self) -> str:
        date_str = self.published_at.strftime("%d/%m/%Y")
        return f"[{date_str}] [{self.source}] {self.title}"


class TCBSNewsAdapter:
    """Lấy tin tức gần đây cho một mã cổ phiếu từ TCBS.

    Kết quả được cache theo TTL để tránh gọi TCBS liên tục.
    """

    def __init__(
        self,
        timeout: float = _TIMEOUT,
        ttl: float = _NEWS_TTL,
    ) -> None:
        self._timeout = timeout
        self._cache: AsyncTTLCache[str, list[NewsItem]] = AsyncTTLCache(
            ttl=ttl,
            name="tcbs_news",
        )

    async def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        """Trả news từ cache nếu còn fresh, otherwise fetch TCBS."""
        cache_key = f"{symbol.upper()}:{limit}"
        return await self._cache.get_or_fetch(
            key=cache_key,
            fetch=lambda: self._fetch_from_tcbs(symbol, limit),
        )

    async def _fetch_from_tcbs(self, symbol: str, limit: int) -> list[NewsItem]:
        """Fetch thật sự — chỉ gọi khi cache miss."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                url = _TCBS_NEWS_URL.format(symbol=symbol.upper())
                resp = await client.get(url, params={**_TCBS_NEWS_PARAMS, "size": limit})
                resp.raise_for_status()
                return _parse_news(resp.json())
        except Exception as exc:
            logger.warning("news_adapter.fetch_failed", extra={"symbol": symbol, "error": str(exc)})
            return []

    def invalidate(self, symbol: str) -> None:
        """Xóa cache khi có breaking news cần refresh ngay."""
        for limit in (3, 5, 10):
            self._cache.invalidate(f"{symbol.upper()}:{limit}")

    def cache_stats(self) -> dict:
        return self._cache.stats()


def _parse_news(data: Any) -> list[NewsItem]:
    """Parse TCBS news response.

    Response shape::

        {
          "listActivityNews": [
            {
              "title": "...",
              "publishDate": "2025-05-20",
              "source": "CafeF",
              "content": "..."
            }
          ]
        }
    """
    items = []
    raw_list = data.get("listActivityNews") or data.get("data") or []
    for entry in raw_list:
        try:
            title = entry.get("title", "").strip()
            if not title:
                continue
            pub_raw = entry.get("publishDate") or entry.get("publishedDate", "")
            try:
                published_at = datetime.fromisoformat(pub_raw)
            except (ValueError, TypeError):
                published_at = datetime.utcnow()
            items.append(
                NewsItem(
                    title=title,
                    published_at=published_at,
                    source=entry.get("source", "unknown"),
                    snippet=entry.get("content", "")[:200],
                )
            )
        except Exception:
            continue
    return items
