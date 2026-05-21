"""Market Regime Service — market segment.

Đánh giá tình trạng thị trường tổng thể (VN-Index + VN30).
Owner: market segment.
Input:  QuoteService (đã có trong repo — dùng VCIAdapter)
Output: MarketRegime DTO → format_for_prompt() → string cho AI

Caching:
- Regime được cache 3 phút (TTL) vì VN-Index cập nhật liên tục trong giờ giao dịch.
- Stampede protection qua AsyncTTLCache.
- Fallback về NEUTRAL nếu fetch fail.

Design: không gọi AI, không import thesis/watchlist/briefing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.market.quote_service import QuoteService
from src.platform.logging import get_logger
from src.platform.ttl_cache import AsyncTTLCache

logger = get_logger(__name__)

MarketState = Literal["RISK_ON", "NEUTRAL", "RISK_OFF", "VOLATILE"]

_VNINDEX = "VNINDEX"
_MARKET_SYMBOLS = ["VNINDEX", "VN30", "HNX30"]
_REGIME_TTL = 3 * 60  # 3 phút
_REGIME_CACHE_KEY = "market_regime"


@dataclass(frozen=True)
class MarketRegime:
    state: MarketState
    vnindex_price: float
    vnindex_change: float  # tuyệt đối
    vnindex_pct: float  # phần trăm
    vn30_pct: float | None
    description: str

    def format_for_prompt(self) -> str:
        sign = "+" if self.vnindex_pct >= 0 else ""
        lines = [
            f"VN-Index: {self.vnindex_price:,.2f} ({sign}{self.vnindex_pct:.2f}%)",
            f"Trạng thái thị trường: {self.state}",
            f"Đánh giá: {self.description}",
        ]
        if self.vn30_pct is not None:
            vn30_sign = "+" if self.vn30_pct >= 0 else ""
            lines.append(f"VN30: {vn30_sign}{self.vn30_pct:.2f}%")
        return "\n".join(lines)


class MarketRegimeService:
    """Đánh giá market regime dựa trên VN-Index và VN30.

    Dùng QuoteService (VCIAdapter) đã có — không cần adapter mới.
    """

    def __init__(
        self,
        quote_service: QuoteService,
        ttl: float = _REGIME_TTL,
    ) -> None:
        self._qs = quote_service
        self._cache: AsyncTTLCache[str, MarketRegime] = AsyncTTLCache(
            ttl=ttl,
            name="market_regime",
        )

    async def get_regime(self) -> MarketRegime:
        """Trả MarketRegime. Cache 3 phút, fallback về NEUTRAL nếu fetch fail."""
        try:
            return await self._cache.get_or_fetch(
                key=_REGIME_CACHE_KEY,
                fetch=self._fetch_regime,
            )
        except Exception as exc:
            logger.warning("market_regime.get_regime_failed", extra={"error": str(exc)})
            return _fallback_regime()

    async def _fetch_regime(self) -> MarketRegime:
        quotes = await self._qs.get_bulk_quotes(_MARKET_SYMBOLS)
        quote_map = {q.ticker: q for q in quotes}
        return _compute_regime(quote_map)

    def invalidate(self) -> None:
        """Force refresh — dùng sau market open/close."""
        self._cache.invalidate(_REGIME_CACHE_KEY)

    def cache_stats(self) -> dict:
        return self._cache.stats()


def _compute_regime(quote_map: dict) -> MarketRegime:
    vni = quote_map.get(_VNINDEX)
    vn30 = quote_map.get("VN30")

    if vni is None:
        return _fallback_regime()

    pct = vni.change_pct
    vn30_pct = vn30.change_pct if vn30 else None

    if pct >= 1.0:
        state: MarketState = "RISK_ON"
        desc = "Thị trường tích cực, dòng tiền vào rộng"
    elif pct >= 0.2:
        state = "NEUTRAL"
        desc = "Thị trường ổn định, không có xu hướng rõ ràng"
    elif pct >= -0.5:
        state = "NEUTRAL"
        desc = "Thị trường thận trọng, dao động nhẹ"
    elif pct >= -1.5:
        state = "RISK_OFF"
        desc = "Thị trường giảm, tâm lý phòng thủ"
    else:
        state = "VOLATILE"
        desc = "Thị trường biến động mạnh, rủi ro cao"

    return MarketRegime(
        state=state,
        vnindex_price=vni.price,
        vnindex_change=vni.change,
        vnindex_pct=pct,
        vn30_pct=vn30_pct,
        description=desc,
    )


def _fallback_regime() -> MarketRegime:
    return MarketRegime(
        state="NEUTRAL",
        vnindex_price=0.0,
        vnindex_change=0.0,
        vnindex_pct=0.0,
        vn30_pct=None,
        description="Không có dữ liệu thị trường",
    )
