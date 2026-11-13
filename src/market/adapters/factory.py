"""Adapter factory — builds the correct adapter chain from settings.

Owner: market segment.

Rules:
    - environment=test or mock_market=True  → MockAdapter
    - otherwise                             → ChainedAdapter(VCI → KBS → VNDirect)

Chính sách VCI-first: mọi dữ liệu thị trường lấy qua VCI (bảng giá, nến ngày, danh sách
mã, chỉ số cơ bản, HĐ tương lai). Trong giờ giao dịch chỉ dùng VCI; KBS/VNDirect chỉ lấp
những mã VCI không trả dữ liệu NGOÀI giờ giao dịch.

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
    from src.market.trading_calendar import VNTradingCalendar

    logger.info("market.adapter", extra={"provider": "vci (kbs->vndirect ngoài giờ GD)"})
    return ChainedAdapter(
        VCIAdapter(),
        KBSAdapter(),
        VNDirectAdapter(),
        fallback_allowed=lambda: not VNTradingCalendar.is_trading_now(),
    )


def build_derivative_quote_source() -> MarketDataAdapter | None:
    """Nguồn giá HĐ tương lai VN30 (bảng giá VCI). None trong test/mock."""
    if settings.environment == "test" or getattr(settings, "mock_market", False):
        return None
    from src.market.adapters.vci import VCIAdapter

    return VCIAdapter()
