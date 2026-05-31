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
  bot/commands/briefing.py    → BriefingService (generate_* + record_feedback)
  bot/commands/briefing.py    → BriefResult (snapshot_id, output)
  readmodel/dashboard_service.py → BriefSnapshot (direct ORM read, no repo)

Context sources injected into BriefingAgent
-------------------------------------------
  watchlist         — WatchlistService.get_tickers(user_id) → tickers
  quotes            — QuoteService.get_bulk_quotes(tickers) → price/volume
  pnl               — PnLService.get_portfolio_pnl(user_id) → unrealised P&L
  thesis            — ThesisService.get_thesis_health(user_id) → thesis status
  sector            — stubbed (SectorRotationAgent.analyze needs sector_performance
                       data not yet available in this flow)
  judge             — ThesisJudgeAgent.judge(theses) → thesis scores
  risk              — PortfolioRiskNarrator.narrate(user_id) → risk summary
  next_action       — NextActionSuggester.suggest(user_id) → next actions
  trend_pred        — TrendPredictionStore.get_recent(user_id) → predictions
  feedback          — DashboardService.get_brief_feedback_summary() → calibration
  agenda            — AgendaService.build_agenda(user_id) → decide/watch/defer
                       (awaited sequentially — see design notes)
  lessons           — LessonService.get_pattern_summary(session, user_id) → patterns
  investor_profile  — InvestorProfileService.get_investor_context() → profile block
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
    session                      — AsyncSession (transaction owner is the caller).
    briefing_agent               — BriefingAgent (AI prompting).
    watchlist_service            — WatchlistService (get tickers).
    pnl_service                  — PnLService (unrealised P&L context). Optional.
    thesis_service               — ThesisService (thesis health context). Optional.
    quote_service                — Any object with get_bulk_quotes(tickers) → list[Quote]. Optional.
    thesis_judge_agent           — ThesisJudgeAgent. Optional.
    sector_agent                 — SectorRotationAgent. Optional. Currently stubbed —
                                   sector context requires sector_performance data
                                   not yet available in the briefing flow.
    risk_narrator                — PortfolioRiskNarrator. Optional.
    next_action_agent            — NextActionSuggester. Optional.
    trend_store                  — TrendPredictionStore. Optional.
    dashboard_service            — DashboardService (feedback calibration). Optional.
                                   Requires session to be set — skipped silently when
                                   session is None. Non-blocking.
    agenda_service               — AgendaService (daily agenda buckets). Optional.
                                   Requires session to be set — skipped silently when
                                   session is None. Non-blocking.
                                   Awaited sequentially (not in gather) to avoid
                                   nested greenlet_spawn conflicts.
    lesson_service               — LessonService (behavioral pattern summary). Optional.
                                   Calls LessonService.get_pattern_summary(session, user_id).
                                   Non-blocking.
    investor_profile_service     — InvestorProfileService (risk appetite + profile).
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
        """Generate the morning brief for a user.

        1. Fetch tickers from watchlist.
        2. Collect context (quotes, PnL, thesis, sector, lessons, profile).
        3. Delegate to BriefingAgent.
        4. Persist snapshot.
        5. Return BriefResult.
        """
        tickers = await self._get_tickers(user_id)
        contexts = await self._collect_contexts(user_id, tickers)
        output = await self._agent.morning_brief(
            user_id=user_id,
            watchlist_tickers=tickers,
            portfolio_context=contexts.get("pnl_context", ""),
            thesis_context=contexts.get("thesis_context", ""),
            sector_context=contexts.get("sector_context", ""),
            judge_context=contexts.get("judge_context", ""),
            risk_context=contexts.get("risk_context", ""),
            next_action_context=contexts.get("next_action_context", ""),
            trend_pred_context=contexts.get("trend_pred_context", ""),
            quotes=contexts.get("quotes", []),
            feedback_summary=contexts.get("feedback_summary", ""),
            investor_profile=contexts.get("investor_profile_context", ""),
            past_lessons=contexts.get("lessons_context", ""),
            agenda_context=contexts.get("agenda_context", ""),
            portfolio_note=contexts.get("portfolio_note", ""),
        )
        text = output.text if hasattr(output, "text") else str(output)
        snapshot_id = await self._persist_snapshot(
            user_id=user_id,
            brief_type="morning",
            brief_text=text,
            tickers=tickers,
        )
        return BriefResult(snapshot_id=snapshot_id, text=text, tickers=tickers, output=output)

    async def generate_eod_brief(self, user_id: str) -> BriefResult:
        """Generate the end-of-day brief for a user."""
        tickers = await self._get_tickers(user_id)
        contexts = await self._collect_contexts(user_id, tickers)
        output = await self._agent.eod_brief(
            user_id=user_id,
            watchlist_tickers=tickers,
            portfolio_context=contexts.get("pnl_context", ""),
            thesis_context=contexts.get("thesis_context", ""),
            sector_context=contexts.get("sector_context", ""),
            judge_context=contexts.get("judge_context", ""),
            risk_context=contexts.get("risk_context", ""),
            next_action_context=contexts.get("next_action_context", ""),
            trend_pred_context=contexts.get("trend_pred_context", ""),
            quotes=contexts.get("quotes", []),
            feedback_summary=contexts.get("feedback_summary", ""),
            investor_profile=contexts.get("investor_profile_context", ""),
            past_lessons=contexts.get("lessons_context", ""),
            agenda_context=contexts.get("agenda_context", ""),
            portfolio_note=contexts.get("portfolio_note", ""),
        )
        text = output.text if hasattr(output, "text") else str(output)
        snapshot_id = await self._persist_snapshot(
            user_id=user_id,
            brief_type="eod",
            brief_text=text,
            tickers=tickers,
        )
        return BriefResult(snapshot_id=snapshot_id, text=text, tickers=tickers, output=output)

    async def record_feedback(
        self,
        brief_snapshot_id: int,
        user_id: str,
        outcome: str,
    ) -> None:
        """Record user feedback for a brief snapshot."""
        from src.briefing.models import BriefFeedback

        feedback = BriefFeedback(
            brief_snapshot_id=brief_snapshot_id,
            user_id=user_id,
            outcome=outcome,
        )
        await self._repo.save_feedback(feedback)

    # ------------------------------------------------------------------
    # Context collection
    # ------------------------------------------------------------------

    async def _get_tickers(self, user_id: str) -> list[str]:
        try:
            return await self._watchlist_service.get_tickers(user_id)
        except Exception as exc:
            logger.warning("briefing.get_tickers.failed", error=str(exc))
            return []

    async def _collect_contexts(
        self,
        user_id: str,
        tickers: list[str],
    ) -> dict[str, Any]:
        """Collect all context blocks in parallel, then agenda sequentially.

        Each builder is wrapped to be fail-safe (returns empty on error).
        _build_agenda_context is awaited after the gather to avoid nested
        greenlet_spawn conflicts from AgendaService.build_agenda's internal
        asyncio.gather on the same session.
        """
        (
            pnl_context,
            thesis_context,
            quotes,
            sector_context,
            judge_context,
            risk_context,
            next_action_context,
            trend_pred_context,
            feedback_summary,
            lessons_context,
            investor_profile_context,
            portfolio_note,
        ) = await asyncio.gather(
            self._build_pnl_context(user_id),
            self._build_thesis_context(user_id),
            self._build_quote_context(tickers),
            self._build_sector_context(user_id),
            self._build_judge_context(user_id),
            self._build_risk_context(user_id),
            self._build_next_action_context(user_id),
            self._build_trend_pred_context(user_id),
            self._build_feedback_summary(user_id),
            self._build_lessons_context(user_id),
            self._build_investor_profile_context(user_id),
            self._build_portfolio_note(user_id),
        )

        # Awaited sequentially — AgendaService.build_agenda itself calls
        # asyncio.gather on the same session; nesting would cause greenlet_spawn.
        agenda_context = await self._build_agenda_context(user_id)

        debug_flags = {
            "ticker_count": len(tickers),
            "has_portfolio": bool(pnl_context),
            "has_thesis": bool(thesis_context),
            "has_lessons": bool(lessons_context),
            "has_investor_profile": bool(investor_profile_context),
            "has_feedback_summary": bool(feedback_summary),
            "has_agenda_context": bool(agenda_context),
            "has_portfolio_note": bool(portfolio_note),
        }
        logger.debug("briefing_agent.morning_brief.calling_ai", **debug_flags)

        return {
            "pnl_context": pnl_context,
            "thesis_context": thesis_context,
            "quotes": quotes,
            "sector_context": sector_context,
            "judge_context": judge_context,
            "risk_context": risk_context,
            "next_action_context": next_action_context,
            "trend_pred_context": trend_pred_context,
            "feedback_summary": feedback_summary,
            "lessons_context": lessons_context,
            "investor_profile_context": investor_profile_context,
            "portfolio_note": portfolio_note,
            "agenda_context": agenda_context,
        }

    # ------------------------------------------------------------------
    # Individual context builders (all fail-safe)
    # ------------------------------------------------------------------

    async def _build_pnl_context(self, user_id: str) -> str:
        if not self._pnl_service:
            return ""
        try:
            return await self._pnl_service.get_portfolio_pnl(user_id) or ""
        except Exception as exc:
            logger.warning("briefing.pnl_context.failed", error=str(exc))
            return ""

    async def _build_thesis_context(self, user_id: str) -> str:
        if not self._thesis_service:
            return ""
        try:
            health = await self._thesis_service.get_thesis_health(user_id)
            if not health:
                return ""
            lines = []
            for t in health:
                ticker = t.get("ticker", "?")
                status = t.get("status", "?")
                score = t.get("health_score", "?")
                lines.append(f"{ticker}: status={status} score={score}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.thesis_context.failed", error=str(exc))
            return ""

    async def _build_quote_context(self, tickers: list[str]) -> list:
        if not self._quote_service or not tickers:
            return []
        try:
            return await self._quote_service.get_bulk_quotes(tickers) or []
        except Exception as exc:
            logger.warning("briefing.quote_context.failed", error=str(exc))
            return []

    async def _build_sector_context(self, user_id: str) -> str:  # noqa: ARG002
        """Stubbed — SectorRotationAgent.analyze requires sector_performance data
        (list of per-sector dicts) that is not yet available in the briefing flow.
        Returns empty string silently until the market segment exposes that feed.
        """
        return ""

    async def _build_judge_context(self, user_id: str) -> str:
        if not self._thesis_judge_agent or not self._thesis_service:
            return ""
        try:
            theses = await self._thesis_service.list_active(user_id)
            if not theses:
                return ""
            return await self._thesis_judge_agent.judge(theses) or ""
        except Exception as exc:
            logger.warning("briefing.judge_context.failed", error=str(exc))
            return ""

    async def _build_risk_context(self, user_id: str) -> str:
        if not self._risk_narrator:
            return ""
        try:
            return await self._risk_narrator.narrate(user_id) or ""
        except Exception as exc:
            logger.warning("briefing.risk_context.failed", error=str(exc))
            return ""

    async def _build_next_action_context(self, user_id: str) -> str:
        if not self._next_action_agent:
            return ""
        try:
            return await self._next_action_agent.suggest(user_id) or ""
        except Exception as exc:
            logger.warning("briefing.next_action_context.failed", error=str(exc))
            return ""

    async def _build_trend_pred_context(self, user_id: str) -> str:
        if not self._trend_store:
            return ""
        try:
            preds = await self._trend_store.get_recent(user_id)
            if not preds:
                return ""
            lines = [f"{p.ticker}: {p.direction} ({p.confidence:.0%})" for p in preds]
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.trend_pred_context.failed", error=str(exc))
            return ""

    async def _build_feedback_summary(self, user_id: str) -> str:
        if not self._dashboard_service or not self._session:
            return ""
        try:
            return await self._dashboard_service.get_brief_feedback_summary(user_id) or ""
        except Exception as exc:
            logger.warning("briefing.feedback_summary.failed", error=str(exc))
            return ""

    async def _build_agenda_context(self, user_id: str) -> str:
        """Build agenda context block from AgendaService.

        Awaited sequentially outside asyncio.gather — AgendaService.build_agenda
        calls asyncio.gather internally on the same AsyncSession, which would cause
        nested greenlet_spawn errors if called from within an outer gather.

        DailyAgendaResult is a Pydantic model — access via attributes (.decide,
        .watch, .defer, .opening_line), not via .get() dict access.
        """
        if not self._agenda_service or not self._session:
            return ""
        try:
            agenda = await self._agenda_service.build_agenda(user_id)
            if not agenda:
                return ""
            parts = []
            if agenda.opening_line:
                parts.append(agenda.opening_line)
            for item in (agenda.decide or []):
                ticker = getattr(item, "ticker", "")
                reason = getattr(item, "reason", "")
                hint = getattr(item, "action_hint", "")
                parts.append(f"DECIDE {ticker}: {reason} → {hint}")
            for item in (agenda.watch or []):
                ticker = getattr(item, "ticker", "")
                reason = getattr(item, "reason", "")
                parts.append(f"WATCH {ticker}: {reason}")
            for item in (agenda.defer or []):
                ticker = getattr(item, "ticker", "")
                parts.append(f"DEFER {ticker}")
            return "\n".join(parts)
        except Exception as exc:
            logger.warning("briefing.agenda_context.failed", error=str(exc))
            return ""

    async def _build_lessons_context(self, user_id: str) -> str:
        """Build behavioral pattern summary from LessonService.

        Calls LessonService.get_pattern_summary(session, user_id) — the session
        argument is required by the LessonService API.
        Returns empty string on any failure.
        """
        if not self._lesson_service:
            return ""
        try:
            summary = await self._lesson_service.get_pattern_summary(self._session, user_id)
            return summary or ""
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
            ctx = await self._investor_profile_service.get_investor_context()
            if ctx is None:
                return ""
            block = getattr(ctx, "to_prompt_block", None)
            return block() if callable(block) else str(ctx)
        except Exception as exc:
            logger.warning("briefing.investor_profile_context.failed", error=str(exc))
            return ""

    async def _build_portfolio_note(
        self,
        user_id: str,  # noqa: ARG002
    ) -> Any:
        """Build PortfolioRiskNote for the narrator.

        Returns None if no portfolio service is available or on any error.
        The narrator inside BriefingAgent handles None gracefully.
        """
        if not self._pnl_service:
            return None
        try:
            note = await self._pnl_service.get_portfolio_risk_note()
            return note or None
        except Exception:
            return None

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
            from src.briefing.models import BriefSnapshot
            snapshot = BriefSnapshot(
                user_id=user_id,
                phase=brief_type,
                content=brief_text,
                tickers=",".join(tickers) if tickers else None,
            )
            saved = await self._repo.save(snapshot)
            return saved.id
        except Exception as exc:
            logger.warning(
                "briefing.snapshot.failed",
                user_id=user_id,
                brief_type=brief_type,
                error=str(exc),
            )
            return None
