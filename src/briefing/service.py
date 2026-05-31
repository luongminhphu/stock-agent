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
  All context-builders run in parallel via asyncio.gather.
- _build_agenda_context() reads from agenda_cache (populated by
  DailyAgendaCompletedEvent at 07:30) — no DB access, no session conflict.
  AgendaService.build_agenda() was the previous approach but caused
  greenlet_spawn errors because it runs DB queries + asyncio.gather on
  the same AsyncSession that BriefingService already holds. The cache
  approach is faster and architecturally correct: the agenda is computed
  once before the brief, not re-computed during it.
- Context-builders are fail-safe: each catches its own errors and returns
  a degraded value (empty string / empty list) rather than aborting the
  brief.
- _build_market_context() renders quotes + sector/risk/judge/next_action/
  trend_pred into a single market_context string that BriefingAgent expects.
  BriefingAgent only accepts pre-rendered strings — never raw lists or
  unknown kwargs.
- _persist_snapshot() stores output.model_dump_json() as BriefSnapshot.content
  so that readmodel.dashboard_service.get_brief_latest() can json.loads() it
  and populate BriefResponse fields (headline, sentiment, key_movers, etc.).
  brief_text (Discord markdown) is kept separately for bot formatting only.

Dependency graph (inbound)
--------------------------
  bot/commands/briefing.py    → BriefingService (generate_* + record_feedback)
  bot/commands/briefing.py    → BriefResult (snapshot_id, output)
  readmodel/dashboard_service.py → BriefSnapshot (direct ORM read, no repo)

Context sources injected into BriefingAgent
-------------------------------------------
  watchlist         — WatchlistService.get_tickers(user_id) → tickers
  quotes            — QuoteService.get_bulk_quotes(tickers) → rendered into market_context
  pnl               — PnLService.get_portfolio_pnl(user_id) → unrealised P&L
  thesis            — ThesisService.get_thesis_health(user_id) → thesis status
  sector            — stubbed (SectorRotationAgent.analyze needs sector_performance
                       data not yet available in this flow)
  judge             — ThesisJudgeAgent.judge(theses) → rendered into market_context
  risk              — PortfolioRiskNarrator.narrate(user_id) → rendered into market_context
  next_action       — NextActionSuggester.suggest(user_id) → rendered into market_context
  trend_pred        — TrendPredictionStore.get_recent(user_id) → rendered into market_context
  feedback          — DashboardService.get_brief_feedback_summary() → calibration
  agenda            — agenda_cache.get_agenda(user_id) → cached CachedAgenda (no DB call)
                       populated by BriefingListener._handle_agenda via DailyAgendaCompletedEvent
  lessons           — LessonService.get_pattern_summary(session, user_id) → patterns
  investor_profile  — InvestorProfileService.get_investor_context() → profile block
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.briefing.agenda_cache import get_agenda
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
    agenda_service               — Kept for backward compat. Ignored — agenda context
                                   is now read from agenda_cache (no DB call needed).
    lesson_service               — LessonService (behavioral pattern summary). Optional.
    investor_profile_service     — InvestorProfileService (risk appetite + profile). Optional.
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
        agenda_service: Any = None,  # kept for backward compat, not used
        lesson_service: Any = None,
        investor_profile_service: Any = None,
        # Accept any extra kwargs from callers that pass unknown params
        # (e.g. sector_rotation_agent) without raising TypeError.
        **_extra: Any,
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
        # agenda_service intentionally not stored — we read from cache instead
        self._lesson_service = lesson_service
        self._investor_profile_service = investor_profile_service
        self._repo = BriefSnapshotRepository(session)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_morning_brief(self, user_id: str) -> BriefResult:
        """Generate the morning brief for a user."""
        tickers = await self._get_tickers(user_id)
        contexts = await self._collect_contexts(user_id, tickers)
        market_context = self._build_market_context(
            quotes=contexts.get("quotes", []),
            sector_context=contexts.get("sector_context", ""),
            judge_context=contexts.get("judge_context", ""),
            risk_context=contexts.get("risk_context", ""),
            next_action_context=contexts.get("next_action_context", ""),
            trend_pred_context=contexts.get("trend_pred_context", ""),
        )
        output = await self._agent.morning_brief(
            market_context=market_context,
            watchlist_tickers=tickers,
            portfolio_context=contexts.get("pnl_context", ""),
            thesis_context=contexts.get("thesis_context", ""),
            past_lessons=contexts.get("lessons_context", ""),
            investor_profile=contexts.get("investor_profile_context", ""),
            feedback_summary=contexts.get("feedback_summary", ""),
            agenda_context=contexts.get("agenda_context", ""),
            portfolio_note=contexts.get("portfolio_note"),
            session=self._session,
            user_id=user_id,
        )
        text = output.text if hasattr(output, "text") else str(output)
        snapshot_id = await self._persist_snapshot(
            user_id=user_id,
            brief_type="morning",
            brief_text=text,
            tickers=tickers,
            output=output,
        )
        return BriefResult(snapshot_id=snapshot_id, text=text, tickers=tickers, output=output)

    async def generate_eod_brief(self, user_id: str) -> BriefResult:
        """Generate the end-of-day brief for a user."""
        tickers = await self._get_tickers(user_id)
        contexts = await self._collect_contexts(user_id, tickers)
        market_context = self._build_market_context(
            quotes=contexts.get("quotes", []),
            sector_context=contexts.get("sector_context", ""),
            judge_context=contexts.get("judge_context", ""),
            risk_context=contexts.get("risk_context", ""),
            next_action_context=contexts.get("next_action_context", ""),
            trend_pred_context=contexts.get("trend_pred_context", ""),
        )
        output = await self._agent.eod_brief(
            market_context=market_context,
            watchlist_tickers=tickers,
            portfolio_context=contexts.get("pnl_context", ""),
            thesis_context=contexts.get("thesis_context", ""),
            past_lessons=contexts.get("lessons_context", ""),
            investor_profile=contexts.get("investor_profile_context", ""),
            feedback_summary=contexts.get("feedback_summary", ""),
            agenda_context=contexts.get("agenda_context", ""),
            portfolio_note=contexts.get("portfolio_note"),
            session=self._session,
            user_id=user_id,
        )
        text = output.text if hasattr(output, "text") else str(output)
        snapshot_id = await self._persist_snapshot(
            user_id=user_id,
            brief_type="eod",
            brief_text=text,
            tickers=tickers,
            output=output,
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
    # Market context renderer
    # ------------------------------------------------------------------

    def _build_market_context(
        self,
        quotes: list,
        sector_context: str = "",
        judge_context: str = "",
        risk_context: str = "",
        next_action_context: str = "",
        trend_pred_context: str = "",
    ) -> str:
        """Render quotes + enrichment blocks into a single market_context string."""
        parts: list[str] = []

        if quotes:
            lines = []
            for q in quotes:
                ticker = getattr(q, "ticker", None) or getattr(q, "symbol", "?")
                price = getattr(q, "price", None) or getattr(q, "close", None) or 0
                change_pct = getattr(q, "change_pct", None) or getattr(q, "pct_change", None) or 0
                volume = getattr(q, "volume", None)
                vol_str = f" vol={volume:,}" if volume else ""
                lines.append(f"{ticker}: {price:,.0f} ({change_pct:+.2f}%){vol_str}")
            parts.append("Giá hiện tại:\n" + "\n".join(lines))

        if judge_context:
            parts.append(f"Thesis scores:\n{judge_context}")

        if risk_context:
            parts.append(f"Risk summary:\n{risk_context}")

        if next_action_context:
            parts.append(f"Next actions:\n{next_action_context}")

        if trend_pred_context:
            parts.append(f"Trend predictions:\n{trend_pred_context}")

        if sector_context:
            parts.append(f"Sector:\n{sector_context}")

        return "\n\n".join(parts) if parts else ""

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
        """Collect all context blocks. All builders run in parallel via asyncio.gather.

        _build_agenda_context reads from in-memory cache — no DB access —
        so it is safe to include in the gather without greenlet_spawn risk.
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
            agenda_context,
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
            self._build_agenda_context(user_id),
        )

        debug_flags = {
            "ticker_count": len(tickers),
            "has_quotes": bool(quotes),
            "has_portfolio": bool(pnl_context),
            "has_thesis": bool(thesis_context),
            "has_lessons": bool(lessons_context),
            "has_investor_profile": bool(investor_profile_context),
            "has_feedback_summary": bool(feedback_summary),
            "has_agenda_context": bool(agenda_context),
            "has_portfolio_note": bool(portfolio_note),
            "has_risk_context": bool(risk_context),
            "has_judge_context": bool(judge_context),
            "has_next_action_context": bool(next_action_context),
        }
        logger.debug("briefing_service.collect_contexts.done", **debug_flags)

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
        not yet available. Returns empty string silently.
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
        """Read agenda context from in-memory cache — zero DB access.

        The agenda is populated by BriefingListener._handle_agenda() when
        DailyAgendaCompletedEvent fires at 07:30 ICT (AgendaBuilderScheduler).

        Why cache instead of AgendaService.build_agenda():
          - AgendaService queries the DB and calls asyncio.gather internally
            on the same AsyncSession that BriefingService holds, causing
            greenlet_spawn / await_only errors at runtime.
          - The agenda is already computed before morning brief runs — reading
            it from cache is both faster and architecturally correct.
          - If the cache is cold (no agenda built yet today), returns "" silently
            and the brief runs without agenda context.
        """
        try:
            cached = get_agenda(user_id)
            if cached is None:
                return ""
            # Use structured buckets when available for richer context
            buckets = getattr(cached, "buckets", None)
            if buckets is not None:
                parts = []
                if cached.summary:
                    parts.append(cached.summary)
                for ticker in (buckets.decide or []):
                    parts.append(f"DECIDE {ticker}")
                for ticker in (buckets.watch or []):
                    parts.append(f"WATCH {ticker}")
                for ticker in (buckets.defer or []):
                    parts.append(f"DEFER {ticker}")
                return "\n".join(parts) if parts else ""
            # Fallback: plain summary string
            return cached.summary or ""
        except Exception as exc:
            logger.warning("briefing.agenda_context.failed", error=str(exc))
            return ""

    async def _build_lessons_context(self, user_id: str) -> str:
        if not self._lesson_service:
            return ""
        try:
            summary = await self._lesson_service.get_pattern_summary(self._session, user_id)
            return summary or ""
        except Exception as exc:
            logger.warning("briefing.lessons_context.failed", error=str(exc))
            return ""

    async def _build_investor_profile_context(self, user_id: str) -> str:
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

    async def _build_portfolio_note(self, user_id: str) -> Any:  # noqa: ARG002
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
        output: Any = None,
    ) -> int | None:
        """Persist a BriefSnapshot to the database.

        content is stored as JSON (output.model_dump_json()) so that
        readmodel.dashboard_service.get_brief_latest() can deserialize it
        into a dict and populate BriefResponse fields correctly.

        brief_text (Discord markdown) is NOT stored here — it is used
        only by the bot formatter after this method returns.

        Fallback chain for content serialization:
          1. output.model_dump_json()  — Pydantic v2 (preferred)
          2. output.json()             — Pydantic v1 compat
          3. str(output)               — last resort (may not be valid JSON)
        """
        try:
            from src.briefing.models import BriefSnapshot

            if output is not None:
                model_dump_json = getattr(output, "model_dump_json", None)
                if callable(model_dump_json):
                    content = model_dump_json()
                else:
                    # Pydantic v1 fallback
                    json_method = getattr(output, "json", None)
                    content = json_method() if callable(json_method) else str(output)
            else:
                content = brief_text

            snapshot = BriefSnapshot(
                user_id=user_id,
                phase=brief_type,
                content=content,
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
