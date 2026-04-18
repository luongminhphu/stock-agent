"""Market segment — symbol registry, quotes, OHLCV, adapters.

Public API:
    Exchange, Sector          — enums
    SymbolInfo                — ticker metadata
    SymbolRegistry            — lookup service
    SymbolNotFoundError
    registry                  — module-level singleton

    Quote                     — real-time quote dataclass
    QuoteService              — inject MarketDataAdapter at startup
    MarketDataAdapter         — ABC for concrete adapters
    QuoteServiceNotConfiguredError

    Candle, Interval          — OHLCV types
    OHLCVService              — historical price service
    OHLCVAdapter              — ABC for OHLCV adapters
    OHLCVServiceNotConfiguredError
"""

from src.market.ohlcv_service import (
    Candle,
    Interval,
    OHLCVAdapter,
    OHLCVService,
    OHLCVServiceNotConfiguredError,
)
from src.market.quote_service import (
    MarketDataAdapter,
    Quote,
    QuoteService,
    QuoteServiceNotConfiguredError,
)
from src.market.registry import (
    Exchange,
    Sector,
    SymbolInfo,
    SymbolNotFoundError,
    SymbolRegistry,
    registry,
)

__all__ = [
    "Exchange",
    "Sector",
    "SymbolInfo",
    "SymbolRegistry",
    "SymbolNotFoundError",
    "registry",
    "Quote",
    "QuoteService",
    "MarketDataAdapter",
    "QuoteServiceNotConfiguredError",
    "Candle",
    "Interval",
    "OHLCVService",
    "OHLCVAdapter",
    "OHLCVServiceNotConfiguredError",
]
