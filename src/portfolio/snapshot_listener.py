"""
PortfolioSnapshotListener — builds enriched portfolio snapshot on demand.

Owner: portfolio segment.

Consumed event : PortfolioSnapshotRequestedEvent
                 (emitted by bot.PortfolioSnapshotScheduler at 08:15 ICT)
Emitted event  : PortfolioSnapshotReadyEvent
                 (consumed by core.IntelligenceEngineListener + briefing.BriefingListener)

Enrichment fields (all backward-compatible — existing consumers
that only read total_positions / total_nav / unrealized_pnl are unaffected):
    unrealized_pnl_pct   — from PortfolioPnl.total_unrealized_pct
    top_exposed_tickers  — top-5 positions sorted desc by market_value
    cash_pct             — placeholder 0.0 (cash model not yet modelled)
    snapshot_phase       — forwarded from PortfolioSnapshotRequestedEvent.phase

Boot: PortfolioSnapshotListener().register() is called in platform bootstrap.
"""
from __future__ import annotations

from src.platform.event_bus import EventBus, get_event_bus
from src.platform.events import (
    PortfolioSnapshotReadyEvent,
    PortfolioSnapshotRequestedEvent,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

_TOP_TICKERS_LIMIT = 5


class PortfolioSnapshotListener:
    """Subscribe to PortfolioSnapshotRequestedEvent → build P&L snapshot → emit Ready.

    Uses PnlService (portfolio segment, read-side) and QuoteService (market segment).
    Both are resolved via bootstrap getters so this class stays import-clean at
    module load time.

    Args:
        bus: EventBus instance. Defaults to get_event_bus() singleton.
    """

    def __init__(self, bus: EventBus | None = None) -> None:
        self._bus = bus or get_event_bus()

    def register(self) -> None:
        self._bus.subscribe_handler(PortfolioSnapshotRequestedEvent, self._handle)
        logger.info("portfolio_snapshot_listener.registered")

    async def _handle(self, event: PortfolioSnapshotRequestedEvent) -> None:
        logger.info(
            "portfolio_snapshot_listener.received",
            user_id=event.user_id,
            phase=event.phase,
            triggered_by=event.triggered_by,
        )

        # ── Resolve singletons lazily (bootstrap must have run first) ─────
        from src.platform.bootstrap import get_pnl_service_class, get_quote_service, get_session_factory

        PnlService = get_pnl_service_class()
        quote_service = get_quote_service()
        session_factory = get_session_factory()

        # ── Build P&L snapshot ─────────────────────────────────────────────
        try:
            async with session_factory() as session:
                svc = PnlService(session=session, quote_service=quote_service)
                pnl = await svc.get_portfolio_pnl(user_id=event.user_id)
        except Exception as exc:
            logger.error(
                "portfolio_snapshot_listener.pnl_failed",
                user_id=event.user_id,
                error=str(exc),
            )
            return

        # ── Derive top_exposed_tickers (sort desc by market_value, top 5) ──
        sorted_positions = sorted(
            pnl.positions,
            key=lambda p: p.market_value,
            reverse=True,
        )
        top_exposed_tickers: tuple[str, ...] = tuple(
            p.ticker for p in sorted_positions[:_TOP_TICKERS_LIMIT]
        )

        # ── Publish enriched PortfolioSnapshotReadyEvent ───────────────────
        ready = PortfolioSnapshotReadyEvent(
            user_id=event.user_id,
            total_positions=len(pnl.positions),
            total_nav=pnl.total_market_value,
            unrealized_pnl=pnl.total_unrealized_pnl,
            unrealized_pnl_pct=round(pnl.total_unrealized_pct, 4),
            top_exposed_tickers=top_exposed_tickers,
            cash_pct=0.0,               # placeholder — cash model not yet modelled
            snapshot_phase=event.phase,
        )
        await self._bus.publish(ready)

        logger.info(
            "portfolio_snapshot_listener.ready_emitted",
            user_id=event.user_id,
            phase=event.phase,
            total_positions=ready.total_positions,
            total_nav=ready.total_nav,
            unrealized_pnl=ready.unrealized_pnl,
            unrealized_pnl_pct=ready.unrealized_pnl_pct,
            top_exposed_count=len(ready.top_exposed_tickers),
            top_exposed_tickers=list(ready.top_exposed_tickers),
            errors=list(pnl.errors.keys()) if pnl.errors else [],
        )
