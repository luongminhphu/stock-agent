"""PortfolioSnapshotListener — portfolio segment.

Owner: portfolio segment.

Listens to: PortfolioSnapshotRequestedEvent
Produces:   PortfolioSnapshotReadyEvent

Aggregates open positions + P&L via PnlService (get_portfolio_pnl),
then publishes a rich PortfolioSnapshotReadyEvent for downstream
consumers (IntelligenceEngineListener, BriefingListener).

No AI calls. No Discord output. Pure data aggregation.

Flow:
    PortfolioSnapshotRequestedEvent  [bot → portfolio]
      → PnlService.get_portfolio_pnl(user_id)
          → PortfolioPnl.positions  (list[PositionPnl])
      → PortfolioSnapshotReadyEvent  [portfolio → core / briefing]
"""
from __future__ import annotations

from src.platform.event_bus import get_event_bus
from src.platform.events import PortfolioSnapshotReadyEvent, PortfolioSnapshotRequestedEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)


class PortfolioSnapshotListener:
    """Subscribe to PortfolioSnapshotRequestedEvent → build snapshot → publish Ready."""

    def register(self) -> None:
        bus = get_event_bus()
        bus.subscribe(PortfolioSnapshotRequestedEvent, self._handle)
        logger.info("portfolio.snapshot_listener.registered")

    async def _handle(self, event: PortfolioSnapshotRequestedEvent) -> None:
        try:
            from src.platform.bootstrap import get_quote_service
            from src.platform.db import AsyncSessionLocal
            from src.portfolio.pnl_service import PnlService

            async with AsyncSessionLocal() as session:
                pnl_svc = PnlService(
                    session=session,
                    quote_service=get_quote_service(),
                )
                portfolio_pnl = await pnl_svc.get_portfolio_pnl(event.user_id)

            positions = portfolio_pnl.positions

            if not positions:
                logger.info(
                    "portfolio.snapshot_listener.no_positions",
                    user_id=event.user_id,
                    phase=event.phase,
                )
                await get_event_bus().publish(
                    PortfolioSnapshotReadyEvent(
                        user_id=event.user_id,
                        total_positions=0,
                        snapshot_phase=event.phase,
                    )
                )
                return

            total_nav = portfolio_pnl.total_market_value
            unrealized_pnl = portfolio_pnl.total_unrealized_pnl
            unrealized_pnl_pct = round(portfolio_pnl.total_unrealized_pct, 2)

            # Top 5 tickers by market_value descending
            sorted_pos = sorted(positions, key=lambda p: p.market_value, reverse=True)
            top_exposed = tuple(p.ticker for p in sorted_pos[:5])

            await get_event_bus().publish(
                PortfolioSnapshotReadyEvent(
                    user_id=event.user_id,
                    total_positions=len(positions),
                    total_nav=round(total_nav, 0),
                    unrealized_pnl=round(unrealized_pnl, 0),
                    unrealized_pnl_pct=unrealized_pnl_pct,
                    top_exposed_tickers=top_exposed,
                    cash_pct=0.0,   # cash model not yet modelled — placeholder
                    snapshot_phase=event.phase,
                )
            )
            logger.info(
                "portfolio.snapshot_listener.ready",
                user_id=event.user_id,
                phase=event.phase,
                total_positions=len(positions),
                total_nav=total_nav,
                unrealized_pnl_pct=unrealized_pnl_pct,
                top_exposed_tickers=list(top_exposed),
                errors=portfolio_pnl.errors,
            )

        except Exception as exc:
            logger.error(
                "portfolio.snapshot_listener.error",
                user_id=event.user_id,
                phase=event.phase,
                error=str(exc),
            )
