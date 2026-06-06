"""ProactiveDiscoveryService — portfolio-aware market discovery orchestrator.

Owner: market segment.
Caller: ProactiveDiscoveryScheduler (bot) via EventBus trigger,
        or direct call from bootstrap job.

Pipeline:
  1. OpportunityScreenService.run()       → screen candidates (market segment)
  2. PortfolioQueryService.get_portfolio() → holdings + sector exposure
  3. SymbolRegistry                        → sector mapping for candidates
  4. _build_portfolio_block()             → formatted portfolio context string
  5. ProactiveDiscoveryAgent.analyze()    → AI synthesis (ai segment)
  6. Emit ProactiveDiscoveryReadyEvent    → bot.ProactiveDiscoverySubscriber

Boundary:
  - Owns pipeline orchestration only — no domain logic, no DB writes.
  - AI call is delegated 100% to ProactiveDiscoveryAgent.
  - Soft-fail: returns False on any error, never raises to caller.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.platform.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


def _build_portfolio_block(portfolio: dict[str, Any]) -> str:
    """Format portfolio dict into a prompt-ready block."""
    lines: list[str] = []

    positions = portfolio.get("positions", [])
    if not positions:
        lines.append("Danh mục: chưa có vị thế nào đang mở.")
    else:
        lines.append(f"Số vị thế đang mở: {len(positions)}")
        for p in positions:
            ticker   = p.get("ticker", "?")
            qty      = p.get("qty", 0)
            avg_cost = p.get("avg_cost", 0)
            pnl_pct  = p.get("unrealized_pnl_pct")
            sector   = p.get("sector", "?")
            pnl_str  = f"{pnl_pct:+.1f}%" if pnl_pct is not None else "N/A"
            lines.append(
                f"  {ticker:<6} | qty={qty} | cost={avg_cost:,.0f} | P&L={pnl_str} | sector={sector}"
            )

    # Sector exposure summary
    sector_exposure: dict[str, int] = {}
    for p in positions:
        s = p.get("sector", "UNKNOWN")
        sector_exposure[s] = sector_exposure.get(s, 0) + 1

    if sector_exposure:
        lines.append("\nSector exposure:")
        for sector, count in sorted(sector_exposure.items(), key=lambda x: -x[1]):
            lines.append(f"  {sector}: {count} mã")

    # Portfolio-level P&L
    summary = portfolio.get("summary", {})
    if summary:
        total_value = summary.get("total_market_value")
        total_pnl   = summary.get("unrealized_pnl_pct")
        if total_value is not None:
            lines.append(f"\nNAV ước tính: {total_value:,.0f} VND")
        if total_pnl is not None:
            lines.append(f"Tổng P&L chưa thực hiện: {total_pnl:+.1f}%")

    return "\n".join(lines) if lines else "Không có thông tin danh mục."


def _build_candidates_block(candidates: list[Any], registry: Any) -> str:
    """Format screen candidates into a prompt-ready block with sector info."""
    if not candidates:
        return "(none)"
    lines: list[str] = []
    for c in candidates:
        sector = "?"
        try:
            info = registry.get(c.ticker)
            if info:
                sector = getattr(info.sector, "value", str(info.sector))
        except Exception:
            pass
        base = c.format_for_prompt()
        lines.append(f"{base}  sector={sector}")
    return "\n".join(lines)


def _picks_to_json(output: Any) -> str:
    """Serialise DiscoveryPick list to JSON string for event transport."""
    import dataclasses
    if output is None:
        return "[]"
    try:
        return json.dumps([dataclasses.asdict(p) for p in output.picks], ensure_ascii=False)
    except Exception:
        return "[]"


class ProactiveDiscoveryService:
    """Orchestrate proactive discovery pipeline. One call per scheduled trigger."""

    def __init__(
        self,
        ai_agent: Any,          # ProactiveDiscoveryAgent
        session_factory: Any,   # AsyncSessionLocal (async context manager)
        quote_service: Any,     # QuoteService
        registry: Any,          # SymbolRegistry instance
    ) -> None:
        self._agent          = ai_agent
        self._session_factory = session_factory
        self._quote_service  = quote_service
        self._registry       = registry

    async def run(self, user_id: str) -> bool:
        """Run full pipeline and emit ProactiveDiscoveryReadyEvent.

        Returns True if event was emitted, False on any failure.
        """
        trading_date = datetime.now(UTC).strftime("%Y-%m-%d")
        logger.info("proactive_discovery_service.run.start", user_id=user_id, trading_date=trading_date)

        try:
            # ── Step 1: Market screen ─────────────────────────────────────────
            from src.market.opportunity_screen_service import OpportunityScreenService
            screen_svc = OpportunityScreenService(quote_service=self._quote_service)
            screen_result = await screen_svc.run()

            if not screen_result.candidates:
                logger.info("proactive_discovery_service.no_candidates", trading_date=trading_date)
                return False

            candidates_block = _build_candidates_block(screen_result.candidates, self._registry)

            # ── Step 2: Portfolio context ─────────────────────────────────────
            portfolio: dict[str, Any] = {}
            try:
                async with self._session_factory() as session:
                    from src.readmodel.portfolio_query_service import PortfolioQueryService
                    # Fetch live prices for holdings
                    pqs = PortfolioQueryService(session)
                    raw_port = await pqs.get_portfolio(user_id=user_id)

                    # Enrich with sector from registry
                    positions_with_sector = []
                    for p in raw_port.get("positions", []):
                        ticker = p.get("ticker", "")
                        sector = "UNKNOWN"
                        try:
                            info = self._registry.get(ticker)
                            if info:
                                sector = getattr(info.sector, "value", str(info.sector))
                        except Exception:
                            pass
                        positions_with_sector.append({**p, "sector": sector})

                    portfolio = {**raw_port, "positions": positions_with_sector}
            except Exception as exc:
                logger.warning(
                    "proactive_discovery_service.portfolio_fetch_failed",
                    error=str(exc),
                    user_id=user_id,
                )
                portfolio = {}

            portfolio_block = _build_portfolio_block(portfolio)

            # ── Step 3: AI synthesis ──────────────────────────────────────────
            output = await self._agent.analyze(
                candidates_block=candidates_block,
                portfolio_block=portfolio_block,
                trading_date=trading_date,
            )

            if output is None:
                logger.warning("proactive_discovery_service.ai_failed", user_id=user_id)
                return False

            # ── Step 4: Emit event ────────────────────────────────────────────
            from src.platform.event_bus import get_event_bus
            from src.platform.events import ProactiveDiscoveryReadyEvent

            event = ProactiveDiscoveryReadyEvent(
                user_id=user_id,
                picks_json=_picks_to_json(output),
                portfolio_gaps=tuple(output.portfolio_gaps),
                market_regime_note=output.market_regime_note,
                avoid_tickers=tuple(output.avoid_tickers),
                picks_count=len(output.picks),
                trading_date=trading_date,
            )
            await get_event_bus().publish(event)

            logger.info(
                "proactive_discovery_service.run.done",
                user_id=user_id,
                picks_count=event.picks_count,
                portfolio_gaps=list(event.portfolio_gaps),
                avoid_count=len(event.avoid_tickers),
                trading_date=trading_date,
            )
            return True

        except Exception as exc:
            logger.error(
                "proactive_discovery_service.run.error",
                user_id=user_id,
                error=str(exc),
            )
            return False
