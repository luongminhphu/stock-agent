"""Portfolio segment — tracks open positions, trade history, and P&L.

Owner: portfolio segment.
Boundary:
  - Owns Position and Trade lifecycle.
  - Consumes QuoteService (market segment) for realtime prices.
  - Does NOT own thesis logic — thesis_id is an optional FK only.
  - Does NOT send Discord notifications — bot/adapter concern.

Public surface:
  PortfolioService   — write-side: buy, sell, list_open
  PnlService         — read-side: unrealized P&L, realized summary, history
"""

from src.portfolio.pnl_service import PnlService, PortfolioPnl, PositionPnl, RealizedSummary
from src.portfolio.service import PortfolioService

__all__ = [
    "PortfolioService",
    "PnlService",
    "PortfolioPnl",
    "PositionPnl",
    "RealizedSummary",
]
