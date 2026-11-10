"""Market data adapters.

Public API:
    VCIAdapter       — primary (trading.vietcap.com.vn, no auth)
    KBSAdapter       — secondary (kbbuddywts.kbsec.com.vn, no auth, realtime)
    VNDirectAdapter  — tertiary (finfo-api.vndirect.com.vn/v4/stock_prices, EOD)
    ChainedAdapter   — tries sources in order, fills missing tickers from the next
    MockAdapter      — deterministic fake data for tests / dev

Usage:
    from src.market.adapters import build_adapter
    adapter = build_adapter()          # reads settings
    quote_service = QuoteService(adapter)
"""

from src.market.adapters.chained import ChainedAdapter
from src.market.adapters.factory import build_adapter
from src.market.adapters.kbs import KBSAdapter
from src.market.adapters.mock import MockAdapter
from src.market.adapters.vci import VCIAdapter
from src.market.adapters.vndirect import VNDirectAdapter

__all__ = [
    "VCIAdapter",
    "KBSAdapter",
    "VNDirectAdapter",
    "ChainedAdapter",
    "MockAdapter",
    "build_adapter",
]
