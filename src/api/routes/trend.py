"""Trend Analysis API route.

Owner: api segment — thin adapter only.
Endpoint: GET /api/v1/trend/ticker/{ticker}

Flow:
    1. Resolve ticker (uppercase)
    2. Build TrendSynthesisService (lazy, request-scoped)
    3. Call TrendSynthesisService.run(ticker)
    4. Return JSON response

Cache strategy (ticker-keyed, no user_id):
    - OHLCV daily candles don’t change within a trading session
    - In market hours  : TTL 30 min (price moves but daily OHLCV stable)
    - Out of market    : TTL 4 h   (data static until next session open)
    - Error responses  : not cached
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from src.api.deps import get_ai_client, get_ohlcv_service
from src.readmodel.cache import DashboardTTLCache

router = APIRouter(prefix="/trend", tags=["trend"])

_cache: DashboardTTLCache = DashboardTTLCache()

# TTL constants (seconds)
_TTL_MARKET_OPEN  = 30 * 60   # 30 min — daily OHLCV stable within session
_TTL_MARKET_CLOSE = 4  * 60 * 60  # 4 h  — data static until next open


def _trend_ttl() -> int:
    """Return cache TTL based on current market hours."""
    try:
        from src.platform.bootstrap import get_quote_service
        guard = get_quote_service()._guard
        return _TTL_MARKET_OPEN if guard.is_market_open() else _TTL_MARKET_CLOSE
    except Exception:
        return _TTL_MARKET_OPEN  # safe default


@router.get("/ticker/{ticker}")
async def get_trend_analysis(
    ticker: str,
    ohlcv_svc: object = Depends(get_ohlcv_service),
    ai_client: object = Depends(get_ai_client),
) -> dict[str, Any]:
    """Return full trend analysis for one ticker.

    Response shape:
    {
        "ticker": "VNM",
        "rrg": {
            "quadrant": "leading",
            "rs_ratio": 102.4,
            "rs_momentum": 101.1,
            "trail_pattern": "ENTERING_LEADING",
            "trail": [{"rs_ratio": ..., "rs_momentum": ...}, ...]
        },
        "indicators": {
            "rsi": 58.2,
            "macd": {"line": 0.12, "signal": 0.08, "histogram": 0.04, "cross": "bullish_cross"},
            "cmf": 0.18,
            "adx": {"value": 27.3, "plus_di": 22.1, "minus_di": 15.4},
            "regime": "TRENDING_UP",
            "composite": 0.68
        },
        "synthesis": {
            "ticker": "VNM",
            "verdict": "BULLISH",
            "action": "ACCUMULATE",
            "confidence": 0.72,
            "signal_summary": "...",
            "rrg_note": "...",
            "macd_note": "...",
            "rsi_note": "...",
            "cmf_note": "...",
            "adx_note": "...",
            "next_watch": "..."
        }
    }
    """
    sym = ticker.strip().upper()

    cached = _cache.get("trend", sym)
    if cached is not None:
        return cached

    from src.ai.agents.trend_synthesis import TrendSynthesisAgent
    from src.market.rrg_service import RRGService
    from src.market.trend_engine import TrendEngine
    from src.market.trend_synthesis_service import TrendSynthesisService

    service = TrendSynthesisService(
        rrg_service=RRGService(ohlcv_svc),  # type: ignore[arg-type]
        trend_engine=TrendEngine(ohlcv_svc),  # type: ignore[arg-type]
        synthesis_agent=TrendSynthesisAgent(ai_client),  # type: ignore[arg-type]
    )

    result = await service.run(sym)
    if "error" not in result:
        _cache.set("trend", sym, result, ttl=_trend_ttl())
    return result
