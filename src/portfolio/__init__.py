"""Portfolio segment — tracks open positions, trade history, and P&L.

Owner: portfolio segment.
Boundary:
  - Owns Position and Trade lifecycle.
  - Consumes QuoteService (market segment) for realtime prices.
  - Does NOT own thesis logic — thesis_id is an optional FK only.
  - Does NOT send Discord notifications — bot/adapter concern.
  - Does NOT import from ai, thesis, watchlist, or briefing segments.

Public surface:
  PortfolioService   — write-side: buy, sell, correct_trade, list_open
  PnlService         — read-side: unrealized P&L, realized summary, history
  PortfolioContext   — typed read-model snapshot for ai/context_builder
  get_portfolio_context() — async factory; single entry point for AI context

Usage by ai/context_builder:
    from src.portfolio import get_portfolio_context, PortfolioContext
    ctx: PortfolioContext = await get_portfolio_context(session, user_id)
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.portfolio.models import PortfolioContext, PositionSummary, RealizedSummary
from src.portfolio.pnl_service import PnlService, PortfolioPnl, PositionPnl
from src.portfolio.service import PortfolioService

__all__ = [
    # Write-side
    "PortfolioService",
    # Read-side services
    "PnlService",
    # PnlService output types
    "PortfolioPnl",
    "PositionPnl",
    "RealizedSummary",
    # AI context contract
    "PortfolioContext",
    "PositionSummary",
    "get_portfolio_context",
]


async def get_portfolio_context(
    session: AsyncSession,
    user_id: str,
    *,
    include_prices: bool = False,
) -> PortfolioContext:
    """Build a PortfolioContext snapshot for a user.

    This is the single entry point for ai/context_builder to consume
    portfolio state without touching service.py or pnl_service.py internals.

    Args:
        session:        AsyncSession — caller manages lifecycle.
        user_id:        Target user.
        include_prices: When True, attempts to enrich positions with current
                        market prices via QuoteService (best-effort; positions
                        that fail price lookup keep market_value=None).
                        Defaults to False for speed in hot paths.

    Returns:
        PortfolioContext — immutable snapshot. Always succeeds; returns
        empty context (has_positions=False) when user has no open positions.
    """
    from src.platform.logging import get_logger

    logger = get_logger(__name__)

    svc = PortfolioService(session)
    pnl_svc = PnlService(session)

    positions = await svc.list_open(user_id)

    if not positions:
        return PortfolioContext(user_id=user_id)

    # --- Build PositionSummary list ---
    summaries: list[PositionSummary] = []
    total_cost_basis = 0.0
    total_realized = 0.0

    for pos in positions:
        cost_basis = pos.avg_cost * pos.qty
        total_cost_basis += cost_basis
        total_realized += pos.realized_pnl
        summaries.append(
            PositionSummary(
                ticker=pos.ticker,
                qty=pos.qty,
                avg_cost=pos.avg_cost,
                sector=pos.sector,
                thesis_id=pos.thesis_id,
            )
        )

    # --- Optional: enrich with live prices ---
    total_market_value: float | None = None
    total_unrealized: float | None = None

    if include_prices:
        try:
            pnl_snapshot = await pnl_svc.get_portfolio_pnl(user_id)
            price_map = {p.ticker: p for p in pnl_snapshot.positions}
            total_market_value = 0.0
            total_unrealized = 0.0
            for s in summaries:
                pos_pnl = price_map.get(s.ticker)
                if pos_pnl and pos_pnl.current_price:
                    mv = pos_pnl.current_price * s.qty
                    upnl = (pos_pnl.current_price - s.avg_cost) * s.qty
                    upnl_pct = (pos_pnl.current_price - s.avg_cost) / s.avg_cost * 100
                    s.market_value = mv
                    s.unrealized_pnl = upnl
                    s.unrealized_pnl_pct = round(upnl_pct, 2)
                    total_market_value += mv
                    total_unrealized += upnl
        except Exception as exc:  # price enrichment is best-effort
            logger.warning("portfolio.context.price_enrich_failed", error=str(exc))
            total_market_value = None
            total_unrealized = None

    # --- Sector weights (based on cost basis when prices unavailable) ---
    basis_for_weights = total_market_value or total_cost_basis
    sector_weights: dict[str, float] = {}
    if basis_for_weights > 0:
        sector_totals: dict[str, float] = {}
        for s in summaries:
            key = s.sector or "không phân loại"
            value = (s.market_value or s.avg_cost * s.qty)
            sector_totals[key] = sector_totals.get(key, 0.0) + value
        sector_weights = {
            k: round(v / basis_for_weights * 100, 1)
            for k, v in sorted(sector_totals.items(), key=lambda x: -x[1])
        }

    return PortfolioContext(
        user_id=user_id,
        open_positions=summaries,
        sector_weights=sector_weights,
        total_cost_basis=round(total_cost_basis, 0),
        total_market_value=round(total_market_value, 0) if total_market_value is not None else None,
        total_unrealized_pnl=round(total_unrealized, 0) if total_unrealized is not None else None,
        total_realized_pnl=round(total_realized, 0),
        position_count=len(summaries),
    )
