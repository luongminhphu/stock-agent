"""Briefing service — morning brief and end-of-day brief generation.

Owner: briefing segment.

This file has TWO responsibilities that must stay separate:

1. BriefingService — the high-level orchestration layer:
   - generate_morning_brief(user_id) → BriefResult
   - generate_eod_brief(user_id) → BriefResult
   - record_feedback(brief_snapshot_id, user_id, outcome) → None
   It coordinates watchlist, quote, AI, thesis, and portfolio services.
   It does NOT contain AI prompting logic — that lives in BriefingAgent.

2. BriefResult — the return value of both generate_* methods.
   It is a simple dataclass, not a domain model.

Design notes
------------
- BriefingService is stateless across calls — it holds no mutable state
  other than the injected collaborators.
- The session is injected from outside (bot command, scheduler) so that
  the caller owns the transaction boundary.
- Each generate_* method is a single logical unit: collect context →
  call agent → persist snapshot → return BriefResult.
- The _collect_contexts() helper is shared between morning and eod flows.
  It calls each context-builder in parallel (asyncio.gather) to minimise
  latency — EXCEPT _build_agenda_context which is awaited sequentially
  to avoid nested greenlet_spawn conflicts (AgendaService.build_agenda
  itself calls asyncio.gather with the same session).
- Context-builders are fail-safe: each catches its own errors and returns
  a degraded value (empty string / empty list) rather than aborting the
  brief.

Dependency graph (inbound)
--------------------------
  bot/commands/briefing.py   → BriefingService (generate_* + record_feedback)
  bot/commands/briefing.py   → BriefResult (snapshot_id, output)
  readmodel/dashboard_service.py → BriefSnapshot (direct ORM read, no repo)

Context sources injected into BriefingAgent
--------------------------------------------
  watchlist        — WatchlistService.get_tickers(user_id) → tickers
  quotes           — QuoteService.get_bulk_quotes(tickers) → price/volume
  pnl              — PnLService.get_portfolio_pnl(user_id) → unrealised P&L
  thesis           — ThesisService.get_thesis_health(user_id) → thesis status
  sector           — stubbed (SectorRotationAgent.analyze needs sector_performance
                     data not yet available in this flow)
  judge            — ThesisJudgeAgent.judge(theses) → thesis scores
  risk             — PortfolioRiskNarrator.narrate(user_id) → risk summary
  next_action      — NextActionSuggester.suggest(user_id) → next actions
  trend_pred       — TrendPredictionStore.get_recent(user_id) → predictions
  feedback         — DashboardService.get_brief_feedback_summary() → calibration
  agenda           — AgendaService.build_agenda(user_id) → decide/watch/defer
                     (awaited sequentially — see design notes)
  lessons          — LessonService.get_pattern_summary(session, user_id) → patterns
  investor_profile — InvestorProfileService.get_investor_context() → profile block
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.briefing.repository import BriefSnapshotRepository
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.ai.agents.briefing import BriefingAgent
    from src.watchlist.service import WatchlistService

logger = get_logger(__name__)


@dataclass
class BriefResult:
    snapshot_id: int | None
    text: str
    tickers: list[str] = field(default_factory=list)
    output: Any | None = field(default=None)  # BriefOutput from BriefingAgent


class BriefingService:
    """Orchestrates context collection and delegates generation to BriefingAgent.

    Constructor arguments
    ---------------------
    session                  — AsyncSession (transaction owner is the caller).
    briefing_agent           — BriefingAgent (AI prompting).
    watchlist_service        — WatchlistService (get tickers).
    pnl_service              — PnLService (unrealised P&L context). Optional.
    thesis_service           — ThesisService (thesis health context). Optional.
    quote_service            — Any object with get_bulk_quotes(tickers) → list[Quote]. Optional.
    thesis_judge_agent       — ThesisJudgeAgent. Optional.
    sector_agent             — SectorRotationAgent. Optional. Currently stubbed —
                               sector context requires sector_performance data
                               not yet available in the briefing flow.
    risk_narrator            — PortfolioRiskNarrator. Optional.
    next_action_agent        — NextActionSuggester. Optional.
    trend_store              — TrendPredictionStore. Optional.
    dashboard_service        — DashboardService (feedback calibration). Optional.
                               Requires session to be set — skipped silently when
                               session is None. Non-blocking.
    agenda_service           — AgendaService (daily agenda buckets). Optional.
                               Requires session to be set — skipped silently when
                               session is None. Non-blocking.
                               Awaited sequentially (not in gather) to avoid
                               nested greenlet_spawn conflicts.
    lesson_service           — LessonService (behavioral pattern summary). Optional.
                               Calls LessonService.get_pattern_summary(session, user_id).
                               Non-blocking.
    investor_profile_service — InvestorProfileService (risk appetite + profile).
                               Optional. Calls get_investor_context().to_prompt_block().
                               Non-blocking.
    """

    def __init__(
        self,
        session: AsyncSession,
        briefing_agent: "BriefingAgent",
        watchlist_service: "WatchlistService",
        pnl_service: Any = None,
        thesis_service: Any = None,
        quote_service: Any = None,
        thesis_judge_agent: Any = None,
        sector_agent: Any = None,
        risk_narrator: Any = None,
        next_action_agent: Any = None,
        trend_store: Any = None,
        dashboard_service: Any = None,
        agenda_service: Any = None,
        lesson_service: Any = None,
        investor_profile_service: Any = None,
    ) -> None:
        self._session = session
        self._agent = briefing_agent
        self._watchlist_service = watchlist_service
        self._pnl_service = pnl_service
        self._thesis_service = thesis_service
        self._quote_service = quote_service
        self._thesis_judge_agent = thesis_judge_agent
        self._sector_agent = sector_agent
        self._risk_narrator = risk_narrator
        self._next_action_agent = next_action_agent
        self._trend_store = trend_store
        self._dashboard_service = dashboard_service
        self._agenda_service = agenda_service
        self._lesson_service = lesson_service
        self._investor_profile_service = investor_profile_service
        self._repo = BriefSnapshotRepository(session)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_morning_brief(self, user_id: str) -> BriefResult:
        """Generate morning brief for user_id."""
        tickers, contexts = await self._collect_contexts(user_id)
        brief_output = await self._agent.morning_brief(
            market_context=contexts.get("quote_context", ""),
            watchlist_tickers=tickers,
            portfolio_context=contexts.get("pnl_context", ""),
            thesis_context=contexts.get("thesis_context", ""),
            extra_context=contexts.get("sector_context", ""),
            feedback_summary=contexts.get("feedback_context", ""),
            agenda_context=contexts.get("agenda_context", ""),
            past_lessons=contexts.get("lessons_context", ""),
            investor_profile=contexts.get("investor_profile_context", ""),
            session=self._session,
            user_id=user_id,
        )
        brief_str = brief_output.text if hasattr(brief_output, "text") else str(brief_output)
        snapshot_id = await self._persist_snapshot(
            user_id=user_id,
            brief_type="morning",
            brief_text=brief_str,
            tickers=tickers,
        )
        return BriefResult(snapshot_id=snapshot_id, text=brief_str, tickers=tickers, output=brief_output)

    async def generate_eod_brief(self, user_id: str) -> BriefResult:
        """Generate end-of-day brief for user_id."""
        tickers, contexts = await self._collect_contexts(user_id)
        brief_output = await self._agent.eod_brief(
            market_context=contexts.get("quote_context", ""),
            watchlist_tickers=tickers,
            portfolio_context=contexts.get("pnl_context", ""),
            thesis_context=contexts.get("thesis_context", ""),
            extra_context=contexts.get("sector_context", ""),
            feedback_summary=contexts.get("feedback_context", ""),
            agenda_context=contexts.get("agenda_context", ""),
            past_lessons=contexts.get("lessons_context", ""),
            investor_profile=contexts.get("investor_profile_context", ""),
            session=self._session,
            user_id=user_id,
        )
        brief_str = brief_output.text if hasattr(brief_output, "text") else str(brief_output)
        snapshot_id = await self._persist_snapshot(
            user_id=user_id,
            brief_type="eod",
            brief_text=brief_str,
            tickers=tickers,
        )
        return BriefResult(snapshot_id=snapshot_id, text=brief_str, tickers=tickers, output=brief_output)

    async def record_feedback(
        self,
        brief_snapshot_id: int,
        user_id: str,
        outcome: str,
    ) -> None:
        """Persist user feedback on a brief snapshot.

        Called by bot/commands/briefing.py BriefFeedbackView._record().
        outcome: 'acted' | 'skipped' | 'noted'
        """
        try:
            from src.briefing.models import BriefFeedback
            feedback = BriefFeedback(
                brief_snapshot_id=brief_snapshot_id,
                user_id=user_id,
                outcome=outcome,
            )
            self._session.add(feedback)
            await self._session.flush()
            logger.info(
                "briefing.feedback.recorded",
                snapshot_id=brief_snapshot_id,
                outcome=outcome,
            )
        except Exception as exc:
            logger.warning(
                "briefing.feedback.failed",
                snapshot_id=brief_snapshot_id,
                outcome=outcome,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Context collection
    # ------------------------------------------------------------------

    async def _collect_contexts(
        self, user_id: str
    ) -> tuple[list[str], dict[str, str]]:
        """Gather all context strings.

        Most builders run concurrently via asyncio.gather.
        _build_agenda_context is awaited SEQUENTIALLY after the gather
        because AgendaService.build_agenda() internally calls asyncio.gather
        on the same AsyncSession — nesting two gather trees on a single session
        triggers greenlet_spawn conflicts in SQLAlchemy async.

        Returns (tickers, context_kwargs) where context_kwargs maps keyword
        argument names expected by BriefingAgent.morning_brief / eod_brief.
        """
        tickers = await self._get_tickers(user_id)

        (
            quote_context,
            pnl_context,
            thesis_context,
            sector_context,
            risk_context,
            next_action_context,
            trend_context,
            feedback_context,
            lessons_context,
            investor_profile_context,
        ) = await asyncio.gather(
            self._build_quote_context(tickers),
            self._build_pnl_context(user_id),
            self._build_thesis_context(user_id),
            self._build_sector_context(tickers),
            self._build_risk_context(user_id),
            self._build_next_action_context(user_id),
            self._build_trend_context(user_id),
            self._build_feedback_context(user_id),
            self._build_lessons_context(user_id),
            self._build_investor_profile_context(user_id),
        )

        # Awaited sequentially — see docstring above.
        agenda_context = await self._build_agenda_context(user_id)

        contexts = {
            "quote_context": quote_context,
            "pnl_context": pnl_context,
            "thesis_context": thesis_context,
            "sector_context": sector_context,
            "risk_context": risk_context,
            "next_action_context": next_action_context,
            "trend_context": trend_context,
            "feedback_context": feedback_context,
            "agenda_context": agenda_context,
            "lessons_context": lessons_context,
            "investor_profile_context": investor_profile_context,
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
            quotes = await self._quote_service.get_bulk_quotes(tickers)
            if not quotes:
                return ""
            lines = []
            for q in quotes:
                ticker = getattr(q, "ticker", "?")
                price = getattr(q, "close", None) or getattr(q, "price", None)
                change = getattr(q, "change_pct", None)
                if price is not None:
                    line = f"{ticker}: {price:,.0f}"
                    if change is not None:
                        line += f" ({change:+.1f}%)"
                    lines.append(line)
            return "Giá:\n" + "\n".join(lines) if lines else ""
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
        """Build thesis health context string from ThesisService.get_thesis_health()."""
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
                verdict = t.get("latest_verdict")
                if verdict:
                    line += f" | verdict: {verdict}"
                lines.append(line)
            return "Thesis:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.thesis_context.failed", error=str(exc))
            return ""

    async def _build_sector_context(self, tickers: list[str]) -> str:  # noqa: ARG002
        """Sector context — stubbed.

        SectorRotationAgent.analyze() requires sector_performance (list of
        {sector, return_1d, return_5d, return_1m, volume_vs_avg}) and
        macro_context strings that are not available in the briefing flow yet.

        This stub returns empty string until the market segment exposes a
        sector-level data source that briefing can consume.

        self._sector_agent is intentionally unused here — kept as a dependency
        injection point for future wiring.
        """
        return ""

    async def _build_risk_context(self, user_id: str) -> str:
        if not self._risk_narrator:
            return ""
        try:
            result = await self._risk_narrator.narrate(user_id)
            return result or ""
        except Exception as exc:
            logger.warning("briefing.risk_context.failed", error=str(exc))
            return ""

    async def _build_next_action_context(self, user_id: str) -> str:
        if not self._next_action_agent:
            return ""
        try:
            result = await self._next_action_agent.suggest(user_id)
            return result or ""
        except Exception as exc:
            logger.warning("briefing.next_action_context.failed", error=str(exc))
            return ""

    async def _build_trend_context(self, user_id: str) -> str:
        if not self._trend_store:
            return ""
        try:
            predictions = await self._trend_store.get_recent(user_id)
            if not predictions:
                return ""
            lines = []
            for p in predictions:
                ticker = getattr(p, "ticker", "?")
                direction = getattr(p, "direction", "?")
                confidence = getattr(p, "confidence", None)
                line = f"{ticker}: {direction}"
                if confidence is not None:
                    line += f" ({confidence:.0%})"
                lines.append(line)
            return "Trend predictions:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.trend_context.failed", error=str(exc))
            return ""

    async def _build_feedback_context(self, user_id: str) -> str:
        """Inject acted_tickers from recent brief feedback for calibration.

        Requires self._dashboard_service and self._session to be set.
        Skipped silently when either is None.
        """
        if not self._dashboard_service or not self._session:
            return ""
        try:
            summary = await self._dashboard_service.get_brief_feedback_summary(user_id)
            if not summary:
                return ""
            acted = getattr(summary, "acted_tickers", None) or []
            if not acted:
                return ""
            return "Recently acted: " + ", ".join(acted)
        except Exception as exc:
            logger.warning("briefing.feedback_context.failed", error=str(exc))
            return ""

    async def _build_agenda_context(self, user_id: str) -> str:
        """Build daily agenda context (decide/watch/defer buckets).

        Awaited sequentially in _collect_contexts (NOT inside asyncio.gather).
        AgendaService.build_agenda() calls asyncio.gather internally on the
        same AsyncSession. Nesting that inside an outer gather causes
        SQLAlchemy greenlet_spawn conflicts.

        Skipped silently when agenda_service or session is None.
        """
        if not self._agenda_service or not self._session:
            return ""
        try:
            result = await self._agenda_service.build_agenda(user_id)
            if not result:
                return ""
            lines = []
            decide = getattr(result, "decide", []) or []
            watch = getattr(result, "watch", []) or []
            defer = getattr(result, "defer", []) or []
            if decide:
                lines.append("DECIDE: " + ", ".join(
                    getattr(item, "ticker", str(item)) for item in decide
                ))
            if watch:
                lines.append("WATCH: " + ", ".join(
                    getattr(item, "ticker", str(item)) for item in watch
                ))
            if defer:
                lines.append("DEFER: " + ", ".join(
                    getattr(item, "ticker", str(item)) for item in defer
                ))
            return "Daily agenda:\n" + "\n".join(lines) if lines else ""
        except Exception as exc:
            logger.warning("briefing.agenda_context.failed", error=str(exc))
            return ""

    async def _build_lessons_context(self, user_id: str) -> str:
        """Build behavioral pattern summary from LessonService.

        Calls LessonService.get_pattern_summary(session, user_id) then
        formats via PatternCounter.format_for_prompt().
        Returns empty string when no lesson data is available yet.
        """
        if not self._lesson_service or not self._session:
            return ""
        try:
            from src.ai.memory.lesson_service import LessonService
            counter = await LessonService.get_pattern_summary(
                session=self._session,
                user_id=user_id,
            )
            return counter.format_for_prompt()
        except Exception as exc:
            logger.warning("briefing.lessons_context.failed", error=str(exc))
            return ""

    async def _build_investor_profile_context(self, user_id: str) -> str:
        """Build investor profile block from InvestorProfileService.

        Calls get_investor_context().to_prompt_block() for a compact
        plain-text block covering risk appetite, style, horizon, and
        latest behavioral snapshot notes.
        Returns empty string on any failure.
        """
        if not self._investor_profile_service:
            return ""
        try:
            ctx = await self._investor_profile_service.get_investor_context(user_id)
            if ctx is None:
                return ""
            block = ctx.to_prompt_block() if hasattr(ctx, "to_prompt_block") else str(ctx)
            return block or ""
        except Exception as exc:
            logger.warning("briefing.investor_profile_context.failed", error=str(exc))
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
        """Persist BriefSnapshot and return its id.

        Non-blocking — failure logs a warning and returns None.
        """
        try:
            snapshot_id = await self._repo.save_snapshot(
                user_id=user_id,
                brief_type=brief_type,
                brief_text=brief_text,
                tickers=tickers,
            )
            return snapshot_id
        except Exception as exc:
            logger.warning(
                "briefing.snapshot.failed",
                user_id=user_id,
                brief_type=brief_type,
                error=str(exc),
            )
            return None
