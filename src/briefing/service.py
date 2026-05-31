"""Briefing service — morning brief and end-of-day brief generation.

Owner: briefing segment.

This file has TWO responsibilities that must stay separate:

1. BriefingService — the high-level orchestrator.
   - generate_morning_brief(user_id) → BriefResult
   - generate_eod_brief(user_id) → BriefResult
   It coordinates watchlist, quote, AI, thesis, and portfolio services.
   It does NOT contain AI prompting logic — that lives in BriefingAgent.

2. BriefingAgentService — thin wrapper that owns the BriefingAgent instance.
   Registered in dependency injection so callers can inject it without knowing
   the underlying AI agent.

Data flow:
  BriefingService._collect_contexts(user_id)
    ├─ watchlist   — WatchlistService.get_tickers(user_id) → tickers
    ├─ quote       — QuoteService.get_quotes(tickers) → price context
    ├─ thesis      — ThesisService.get_thesis_health(user_id) → thesis context
    ├─ thesis_judge— ThesisJudgeAgent.judge(theses) → AI-scored thesis context
    ├─ sector      — SectorAnalysisAgent.analyse(tickers) → sector context
    └─ pnl         — PnLService.get_portfolio_pnl(user_id) → unrealised P&L

All context strings are compact single-strings injected into BriefingAgent.

Design notes:
  - BriefingService does not own an AsyncSession; it delegates DB work to
    injected service objects.
  - Each context builder method catches exceptions independently so that a
    failure in one data source (e.g. quote API) does not abort the entire brief.
  - ThesisBriefContext is the rich structured output from ThesisJudgeAgent.
    It is stored on BriefResult for downstream consumers (bot formatter).
  - portfolio_service is intentionally NOT injected here. Portfolio data is
    accessed via PnLService only, keeping portfolio segment boundary intact.
  - SectorAnalysisAgent and ThesisJudgeAgent are optional; if not injected
    their context strings are empty and the brief degrades gracefully.
  - Context sources injected into BriefingAgent
    are compact string injected into BriefingAgent context.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.ai.agents.briefing import BriefingAgent
    from src.ai.agents.sector_analysis import SectorAnalysisAgent
    from src.ai.agents.thesis_judge import ThesisJudgeAgent
    from src.portfolio.pnl_service import PnlService
    from src.thesis.service import ThesisService
    from src.watchlist.service import WatchlistService

logger = get_logger(__name__)


@dataclass
class BriefResult:
    """Output of generate_morning_brief / generate_eod_brief."""

    snapshot_id: int | None
    text: str
    tickers: list[str] = field(default_factory=list)
    thesis_brief_context: object | None = None  # ThesisBriefContext if available


class BriefingService:
    """Orchestrates context collection and delegates to BriefingAgent.

    Constructor arguments
    ---------------------
    briefing_agent     — BriefingAgent (AI prompting).
    watchlist_service  — WatchlistService (get tickers).
    pnl_service        — PnlService (unrealised P&L context). Optional.
    thesis_service     — ThesisService (thesis health context). Optional.
    quote_service      — Any object with get_quotes(tickers) -> dict. Optional.
    thesis_judge_agent — ThesisJudgeAgent. Optional.
    sector_agent       — SectorAnalysisAgent. Optional.
    repo               — BriefingRepository for persisting snapshots. Optional.
    """

    def __init__(
        self,
        briefing_agent: "BriefingAgent",
        watchlist_service: "WatchlistService",
        pnl_service: "PnlService | None" = None,
        thesis_service: "ThesisService | None" = None,
        quote_service: object | None = None,
        thesis_judge_agent: "ThesisJudgeAgent | None" = None,
        sector_agent: "SectorAnalysisAgent | None" = None,
        repo: object | None = None,
    ) -> None:
        self._agent = briefing_agent
        self._watchlist_service = watchlist_service
        self._pnl_service = pnl_service
        self._thesis_service = thesis_service
        self._quote_service = quote_service
        self._thesis_judge_agent = thesis_judge_agent
        self._sector_agent = sector_agent
        self._repo = repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_morning_brief(self, user_id: str) -> BriefResult:
        """Generate morning brief for user_id."""
        tickers, contexts = await self._collect_contexts(user_id)
        brief_text = await self._agent.morning_brief(
            user_id=user_id,
            tickers=tickers,
            **contexts,
        )
        snapshot_id = await self._persist_snapshot(
            user_id=user_id,
            brief_type="morning",
            brief_text=brief_text,
            tickers=tickers,
        )
        return BriefResult(snapshot_id=snapshot_id, text=brief_text, tickers=tickers)

    async def generate_eod_brief(self, user_id: str) -> BriefResult:
        """Generate end-of-day brief for user_id."""
        tickers, contexts = await self._collect_contexts(user_id)
        brief_text = await self._agent.eod_brief(
            user_id=user_id,
            tickers=tickers,
            **contexts,
        )
        snapshot_id = await self._persist_snapshot(
            user_id=user_id,
            brief_type="eod",
            brief_text=brief_text,
            tickers=tickers,
        )
        return BriefResult(snapshot_id=snapshot_id, text=brief_text, tickers=tickers)

    # ------------------------------------------------------------------
    # Context collection
    # ------------------------------------------------------------------

    async def _collect_contexts(
        self, user_id: str
    ) -> tuple[list[str], dict[str, str]]:
        """Gather all context strings concurrently.

        Returns (tickers, context_kwargs) where context_kwargs maps keyword
        argument names expected by BriefingAgent.morning_brief / eod_brief.
        """
        tickers = await self._get_tickers(user_id)

        (
            quote_context,
            pnl_context,
            thesis_context,
            sector_context,
        ) = await asyncio.gather(
            self._build_quote_context(tickers),
            self._build_pnl_context(user_id),
            self._build_thesis_context(user_id),
            self._build_sector_context(tickers),
        )

        contexts = {
            "quote_context": quote_context,
            "pnl_context": pnl_context,
            "thesis_context": thesis_context,
            "sector_context": sector_context,
        }
        return tickers, contexts

    async def _get_tickers(self, user_id: str) -> list[str]:
        try:
            return await self._watchlist_service.get_tickers(user_id)
        except Exception as exc:
            logger.warning("briefing.get_tickers.failed", error=str(exc))
            return []

    async def _build_quote_context(self, tickers: list[str]) -> str:
        if not self._quote_service or not tickers:
            return ""
        try:
            quotes = await self._quote_service.get_quotes(tickers)
            if not quotes:
                return ""
            lines = []
            for ticker, q in quotes.items():
                price = getattr(q, "close", None) or getattr(q, "price", None)
                change = getattr(q, "change_pct", None)
                if price is not None:
                    line = f"{ticker}: {price:,.0f}"
                    if change is not None:
                        line += f" ({change:+.1f}%)"
                    lines.append(line)
            return "Gi\u00e1:\n" + "\n".join(lines) if lines else ""
        except Exception as exc:
            logger.warning("briefing.quote_context.failed", error=str(exc))
            return ""

    async def _build_pnl_context(self, user_id: str) -> str:
        if not self._pnl_service:
            return ""
        try:
            pnl = await self._pnl_service.get_portfolio_pnl(user_id)
            if not pnl or not pnl.positions:
                return ""
            lines = []
            for pos in pnl.positions:
                lines.append(f"{pos.ticker}: {pos.unrealized_pct:+.1f}%")
            return "P&L:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.pnl_context.failed", error=str(exc))
            return ""

    async def _build_thesis_context(self, user_id: str) -> str:
        """Build thesis health context string.

        Uses ThesisService.get_thesis_health() which returns a list of dicts.
        Also injects the latest review verdict for each thesis if available.
        """
        if not self._thesis_service:
            return ""
        try:
            theses = await self._thesis_service.get_thesis_health(user_id)
            if not theses:
                return ""
            lines = []
            for t in theses:
                ticker = t.get("ticker", "?")
                days = t.get("days_since_review")
                assumption_count = t.get("assumption_count", 0)
                line = f"{ticker}: {assumption_count} assumptions"
                if days is not None:
                    line += f", last review {days}d ago"
                lines.append(line)
            return "Thesis:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.thesis_context.failed", error=str(exc))
            return ""

    async def _build_thesis_judge_context(self, user_id: str) -> str:
        """Build thesis judge context string injected into BriefingAgent context.

        Calls ThesisJudgeAgent.judge() with the active theses for user_id.
        Returns a compact string or empty string on failure/no-op.
        """
        if not self._thesis_judge_agent or not self._thesis_service:
            return ""
        try:
            theses = await self._thesis_service.list_active(user_id)
            if not theses:
                return ""
            result = await self._thesis_judge_agent.judge(theses)
            if not result:
                return ""
            lines = []
            for ticker, score in result.items():
                lines.append(f"{ticker}: score={score}")
            return "Thesis Judge:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.thesis_judge_context.failed", error=str(exc))
            return ""

    async def _build_sector_context(self, tickers: list[str]) -> str:
        if not self._sector_agent or not tickers:
            return ""
        try:
            result = await self._sector_agent.analyse(tickers)
            if not result:
                return ""
            lines = [f"{ticker}: {sector}" for ticker, sector in result.items()]
            return "Sector:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.sector_context.failed", error=str(exc))
            return ""

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist_snapshot(
        self,
        user_id: str,
        brief_type: str,
        brief_text: str,
        tickers: list[str],
    ) -> int | None:
        if not self._repo:
            return None
        try:
            from src.briefing.models import BriefingSnapshot
            snapshot = BriefingSnapshot(
                user_id=user_id,
                brief_type=brief_type,
                content=brief_text,
                tickers=tickers,
            )
            return await self._repo.save(snapshot)
        except Exception as exc:
            logger.warning("briefing.persist_snapshot.failed", error=str(exc))
            return None
