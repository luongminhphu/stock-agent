"""Adapter factory — builds the correct adapter chain from settings.

Owner: market segment.

Rules:
    - environment=test or mock_market=True  → MockAdapter
    - otherwise                             → ChainedAdapter(VCI → KBS → VNDirect)

This is the ONLY place where adapter selection logic lives.
All other code calls build_adapter() and works against MarketDataAdapter.
"""

from __future__ import annotations

from src.market.quote_service import MarketDataAdapter
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
    from src.market.adapters.kbs import KBSAdapter
    from src.market.adapters.vci import VCIAdapter
    from src.market.adapters.vndirect import VNDirectAdapter

    logger.info("market.adapter", extra={"provider": "chained(vci->kbs->vndirect)"})
    return ChainedAdapter(VCIAdapter(), KBSAdapter(), VNDirectAdapter())


def build_reference_adapter() -> MarketDataAdapter | None:
    """Nguồn đối chứng độc lập với nguồn chính — dùng khi chốt giá đóng cửa.

    Trả None trong test/mock (không có gì để đối chứng).
    """
    if settings.environment == "test" or getattr(settings, "mock_market", False):
        return None
    from src.market.adapters.kbs import KBSAdapter

    return KBSAdapter()
