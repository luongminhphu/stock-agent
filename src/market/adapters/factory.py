"""Adapter factory — builds the correct adapter chain from settings.

Owner: market segment.

Rules:
    - environment=test or mock_market=True  → MockAdapter
    - otherwise                             → ChainedAdapter(VCI → VNDirect)

This is the ONLY place where adapter selection logic lives.
All other code calls build_adapter() and works against MarketDataAdapter.
"""

from __future__ import annotations

from src.market.quote_service import MarketDataAdapter, QuoteService
from src.platform.config import settings
from src.platform.logging import get_logger

logger = get_logger(__name__)


def build_adapter() -> MarketDataAdapter:
    """Return the configured MarketDataAdapter for this environment."""
    # Lazy imports to avoid loading httpx in test-only contexts unnecessarily
    if settings.environment == "test" or getattr(settings, "mock_market", False):
        from src.market.adapters.mock import MockAdapter

        logger.info("market.adapter", extra={"provider": "mock"})
        return MockAdapter()

    from src.market.adapters.chained import ChainedAdapter
    from src.market.adapters.vci import VCIAdapter
    from src.market.adapters.vndirect import VNDirectAdapter

    primary = VCIAdapter()
    secondary = VNDirectAdapter()
    logger.info("market.adapter", extra={"provider": "chained(vci->vndirect)"})
    return ChainedAdapter(primary=primary, secondary=secondary)


def create_trend_context_fetcher(quote_service: QuoteService) -> "TrendContextFetcher":
    """Wire TrendContextFetcher từ các adapter đã có.

    Usage::

        quote_svc = QuoteService(adapter=build_adapter())
        fetcher = create_trend_context_fetcher(quote_svc)
        trend_engine = TrendEngine(ohlcv_service, context_fetcher=fetcher, ...)
    """
    from src.market.news_adapter import TCBSNewsAdapter
    from src.market.market_regime import MarketRegimeService
    from src.market.trend_context_fetcher import TrendContextFetcher

    return TrendContextFetcher(
        news_adapter=TCBSNewsAdapter(),
        regime_service=MarketRegimeService(quote_service),
    )
