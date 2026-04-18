"""Market data adapters.

Public API:
    VCIAdapter       — primary (trading.vietcap.com.vn, no auth)
    VNDirectAdapter  — secondary (finfo-api.vndirect.com.vn, no auth)
    ChainedAdapter   — tries primary, falls back to secondary on error
    MockAdapter      — deterministic fake data for tests / dev

Usage:
    from src.market.adapters import build_adapter
    adapter = build_adapter()          # reads settings
    quote_service = QuoteService(adapter)
"""

from src.market.adapters.vci import VCIAdapter
from src.market.adapters.vndirect import VNDirectAdapter
from src.market.adapters.chained import ChainedAdapter
from src.market.adapters.mock import MockAdapter
from src.market.adapters.factory import build_adapter

__all__ = [
    "VCIAdapter",
    "VNDirectAdapter",
    "ChainedAdapter",
    "MockAdapter",
    "build_adapter",
]
