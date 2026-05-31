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
  latency.
- Context-builders are fail-safe: each catches its own errors and returns
  a degraded value (empty string / empty list) rather than aborting the
  brief.

Dependency graph (inbound)
--------------------------
  bot/commands/briefing.py   → BriefingService (generate_* + record_feedback)
  bot/commands/briefing.py   → BriefResult (snapshot_id)
  readmodel/dashboard_service.py → BriefSnapshot (direct ORM read, no repo)

Context sources injected into BriefingAgent
--------------------------------------------
  watchlist   — WatchlistService.get_items(user_id) → tickers
  quotes      — QuoteService.batch_get_quotes(tickers) → price/volume
  pnl         — PnLService.get_portfolio_pnl(user_id) → unrealised P&L
  thesis      — ThesisService.get_thesis_health(user_id) → thesis status
  sector      — SectorRotationAgent.analyse(tickers) → sector flow
  judge       — ThesisJudgeAgent.judge(theses) → thesis scores
  risk        — PortfolioRiskNarrator.narrate(user_id) → risk summary
  next_action — NextActionSuggester.suggest(user_id) → next actions
  trend_pred  — TrendPredictionStore.get_recent(user_id) → predictions
  feedback    — DashboardService.get_brief_feedback_summary() → calibration
  agenda      — AgendaService.build_agenda(user_id) → decide/watch/defer

Wave B.1 (AgendaService integration):
  - AgendaService.build_agenda(user_id) → DailyAgendaResult
    (decide / watch / defer buckets).  Result is formatted by
    _build_agenda_context() and injected as "Daily agenda" block.
  - Passed into BriefingService so morning/eod briefs include today's agenda context
    for smarter action mapping (DECIDE → ACT_TODAY, WATCH → MONITOR).
  - AgendaService requires session — skipped silently when session is None.

Wave B.1.1 (recently-acted tickers connector):
  _build_feedback_context() now also calls DashboardService.get_acted_tickers_recent()
  to inject recently acted tickers into the AI context.
  This prevents redundant ACT_TODAY for tickers the user already acted on,
  surfacing other watchlist candidates instead.
  Đây vẫn là join ở tầng trình bày; contract của BriefingService và
  AgendaService vẫn độc lập — BriefingService chỉ đọc kết quả từ AgendaService,
  không phụ thuộc vào implementation của nó.

Wave B.2 (agenda buckets connector):
  - _build_agenda_context() calls build_agenda(user_id) and
                                 formats DailyAgendaResult (decide/watch/defer) into a
                                 compact string injected into BriefingAgent context.
                                 Requires session to be set — skipped silently when
                                 session is None. Non-blocking.
                                 Pass None (default) to skip — preserves existing behavior.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.briefing_agent import BriefingAgent
from src.ai.agents.thesis_judge_agent import ThesisJudgeAgent
from src.briefing.models import BriefFeedback, BriefSnapshot
from src.briefing.repository import BriefSnapshotRepository
from src.platform.logging import get_logger
from src.watchlist.service import WatchlistService

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Return value
# ---------------------------------------------------------------------------


@dataclass
class BriefResult:
    """Return value of BriefingService.generate_morning_brief / generate_eod_brief.

    Attributes:
        text        — Full brief text ready to send to Discord.
        snapshot_id — DB id of the persisted BriefSnapshot, or None when the
                      session was not available (scheduler dry-run, tests).
        tickers     — Tickers covered in this brief (for the feedback view).
        phase       — "morning" or "eod".
        metadata    — Extra context dict for logging / debugging.
    """

    text: str
    snapshot_id: int | None = None
    tickers: list[str] = field(default_factory=list)
    phase: str = "morning"
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class BriefingService:
    """Orchestrates morning and end-of-day brief generation.

    Injected collaborators
    ----------------------
    Required:
      watchlist_service  — resolves the user's watchlist tickers.
      quote_service      — provides price / volume / change data.
      briefing_agent     — the AI agent that generates the brief text.

    Optional (degrade gracefully when None):
      pnl_service              — unrealised P&L context.
      thesis_service           — thesis health context.
      session                  — SQLAlchemy session for snapshot persistence
                                 and feedback recording.
      sector_rotation_agent    — sector flow context.
      thesis_judge_agent       — thesis scoring context.
      portfolio_risk_narrator  — portfolio risk narrative.
      next_action_suggester    — next action suggestions.
      trend_prediction_store   — trend predictions.
      agenda_service           — daily agenda (decide/watch/defer buckets).

    Wave B.2 (AgendaService):
      agenda_service: AgendaService | None = None
                                 Passed into BriefingService so morning/eod briefs include today's agenda context
                                 for smarter action mapping (DECIDE → ACT_TODAY, WATCH → MONITOR).
                                 _build_agenda_context() calls build_agenda(user_id) and
                                 formats DailyAgendaResult (decide/watch/defer) into a
                                 compact string injected into BriefingAgent context.
                                 Requires session to be set — skipped silently when
                                 session is None. Non-blocking.
                                 Pass None (default) to skip — preserves existing behavior.
    """

    def __init__(
        self,
        watchlist_service: WatchlistService,
        quote_service: object,
        briefing_agent: BriefingAgent,
        pnl_service: object | None = None,
        thesis_service: object | None = None,
        session: AsyncSession | None = None,
        sector_rotation_agent: object | None = None,
        thesis_judge_agent: ThesisJudgeAgent | None = None,
        portfolio_risk_narrator: object | None = None,
        next_action_suggester: object | None = None,
        trend_prediction_store: object | None = None,
        agenda_service: object | None = None,
    ) -> None:
        self._watchlist_service = watchlist_service
        self._quote_service = quote_service
        self._agent = briefing_agent
        self._pnl_service = pnl_service
        self._thesis_service = thesis_service
        self._session = session
        self._repo = BriefSnapshotRepository(session) if session is not None else None
        self._sector_rotation_agent = sector_rotation_agent
        self._thesis_judge_agent = thesis_judge_agent
        self._portfolio_risk_narrator = portfolio_risk_narrator
        self._next_action_suggester = next_action_suggester
        self._trend_prediction_store = trend_prediction_store
        self._agenda_service = agenda_service

    async def generate_morning_brief(self, user_id: str) -> BriefResult:
        ctx = await self._collect_contexts(user_id, phase="morning")
        logger.info(
            "briefing.generate_morning",
            user_id=user_id,
            ticker_count=len(ctx.get("tickers", [])),
            has_pnl=bool(ctx.get("pnl_context")),
            has_thesis=bool(ctx.get("thesis_context")),
            has_sector=bool(ctx.get("sector_context")),
            has_judge=bool(ctx.get("judge_context")),
            has_risk=bool(ctx.get("risk_context")),
            has_next_action=bool(ctx.get("next_action_context")),
            has_trend_prediction=bool(ctx.get("trend_prediction_context")),
            has_feedback_summary=bool(ctx["feedback_summary"]),
        )
        brief_text = await self._agent.generate_morning_brief(
            tickers=ctx["tickers"],
            quote_context=ctx["quote_context"],
            pnl_context=ctx.get("pnl_context", ""),
            thesis_context=ctx.get("thesis_context", ""),
            sector_context=ctx.get("sector_context", ""),
            judge_context=ctx.get("judge_context", ""),
            risk_context=ctx.get("risk_context", ""),
            next_action_context=ctx.get("next_action_context", ""),
            trend_prediction_context=ctx.get("trend_prediction_context", ""),
            feedback_summary=ctx["feedback_summary"],
            agenda_context=ctx.get("agenda_context", ""),
        )
        snapshot_id = await self._persist_snapshot(
            user_id=user_id,
            phase="morning",
            brief_text=brief_text,
            tickers=ctx["tickers"],
        )
        return BriefResult(
            text=brief_text,
            snapshot_id=snapshot_id,
            tickers=ctx["tickers"],
            phase="morning",
        )

    async def generate_eod_brief(self, user_id: str) -> BriefResult:
        ctx = await self._collect_contexts(user_id, phase="eod")
        logger.info(
            "briefing.generate_eod",
            user_id=user_id,
            ticker_count=len(ctx.get("tickers", [])),
            has_pnl=bool(ctx.get("pnl_context")),
            has_thesis=bool(ctx.get("thesis_context")),
            has_sector=bool(ctx.get("sector_context")),
            has_judge=bool(ctx.get("judge_context")),
            has_risk=bool(ctx.get("risk_context")),
            has_next_action=bool(ctx.get("next_action_context")),
            has_trend_prediction=bool(ctx.get("trend_prediction_context")),
            has_feedback_summary=bool(ctx["feedback_summary"]),
        )
        brief_text = await self._agent.generate_eod_brief(
            tickers=ctx["tickers"],
            quote_context=ctx["quote_context"],
            pnl_context=ctx.get("pnl_context", ""),
            thesis_context=ctx.get("thesis_context", ""),
            sector_context=ctx.get("sector_context", ""),
            judge_context=ctx.get("judge_context", ""),
            risk_context=ctx.get("risk_context", ""),
            next_action_context=ctx.get("next_action_context", ""),
            trend_prediction_context=ctx.get("trend_prediction_context", ""),
            feedback_summary=ctx["feedback_summary"],
            agenda_context=ctx.get("agenda_context", ""),
        )
        snapshot_id = await self._persist_snapshot(
            user_id=user_id,
            phase="eod",
            brief_text=brief_text,
            tickers=ctx["tickers"],
        )
        return BriefResult(
            text=brief_text,
            snapshot_id=snapshot_id,
            tickers=ctx["tickers"],
            phase="eod",
        )

    # ------------------------------------------------------------------
    # Context collection
    # ------------------------------------------------------------------

    async def _collect_contexts(
        self, user_id: str, phase: str
    ) -> dict[str, Any]:
        """Gather all context sources in parallel.

        Each builder is fire-and-forget: errors are caught internally and
        return a degraded empty value so the brief still generates.
        """
        (
            tickers,
            quote_context,
            pnl_context,
            thesis_context,
            sector_context,
            judge_context,
            risk_context,
            next_action_context,
            trend_prediction_context,
            feedback_summary,
            agenda_context,
        ) = await asyncio.gather(
            self._build_tickers(user_id),
            self._build_quote_context(user_id),
            self._build_pnl_context(user_id),
            self._build_thesis_context(user_id),
            self._build_sector_context(user_id),
            self._build_judge_context(user_id),
            self._build_risk_context(user_id),
            self._build_next_action_context(user_id),
            self._build_trend_prediction_context(user_id),
            self._build_feedback_context(user_id),
            self._build_agenda_context(user_id),
        )
        logger.info(
            "briefing.collect_contexts_complete",
            user_id=user_id,
            phase=phase,
            ticker_count=len(tickers),
        )
        return {
            "tickers": tickers,
            "quote_context": quote_context,
            "pnl_context": pnl_context,
            "thesis_context": thesis_context,
            "sector_context": sector_context,
            "judge_context": judge_context,
            "risk_context": risk_context,
            "next_action_context": next_action_context,
            "trend_prediction_context": trend_prediction_context,
            "feedback_summary": feedback_summary,
            "agenda_context": agenda_context,
        }

    # ------------------------------------------------------------------
    # Individual context builders (all fail-safe)
    # ------------------------------------------------------------------

    async def _build_tickers(self, user_id: str) -> list[str]:
        try:
            items = await self._watchlist_service.get_items(user_id)
            return [item.ticker for item in items]
        except Exception as exc:
            logger.warning("briefing.tickers_failed", user_id=user_id, error=str(exc))
            return []

    async def _build_quote_context(self, user_id: str) -> str:
        try:
            items = await self._watchlist_service.get_items(user_id)
            tickers = [item.ticker for item in items]
            if not tickers:
                return ""
            quotes = await self._quote_service.batch_get_quotes(tickers)
            return self._format_quotes(quotes)
        except Exception as exc:
            logger.warning(
                "briefing.quote_context_failed", user_id=user_id, error=str(exc)
            )
            return ""

    async def _build_pnl_context(self, user_id: str) -> str:
        if self._pnl_service is None:
            return ""
        try:
            return await self._pnl_service.get_portfolio_pnl_context(user_id)
        except Exception as exc:
            logger.debug(
                "briefing.pnl_context_failed", user_id=user_id, error=str(exc)
            )
            return ""

    async def _build_thesis_context(self, user_id: str) -> str:
        if self._thesis_service is None:
            return ""
        try:
            health = await self._thesis_service.get_thesis_health(user_id)
            if not health:
                return ""
            lines = []
            for t in health:
                line = (
                    f"[{t['id']}] {t['ticker']} ({t['status']}) "
                    f"— {t['entry_thesis'][:120]}"
                )
                if t.get("days_since_review") is not None:
                    line += f" | last review: {t['days_since_review']}d ago"
                if t.get("assumption_count"):
                    line += f" | assumptions: {t['assumption_count']}"
                lines.append(line)
            return "Active theses:\n" + "\n".join(lines)
        except Exception as exc:
            logger.debug(
                "briefing.thesis_context_failed", user_id=user_id, error=str(exc)
            )
            return ""

    async def _build_sector_context(self, user_id: str) -> str:
        if self._sector_rotation_agent is None:
            return ""
        try:
            items = await self._watchlist_service.get_items(user_id)
            tickers = [item.ticker for item in items]
            if not tickers:
                return ""
            result = await self._sector_rotation_agent.analyse(tickers)
            return str(result) if result else ""
        except Exception as exc:
            logger.debug(
                "briefing.sector_context_failed", user_id=user_id, error=str(exc)
            )
            return ""

    async def _build_judge_context(self, user_id: str) -> str:
        if self._thesis_judge_agent is None or self._thesis_service is None:
            return ""
        try:
            theses = await self._thesis_service.list_active(user_id)
            if not theses:
                return ""
            result = await self._thesis_judge_agent.judge(theses)
            return str(result) if result else ""
        except Exception as exc:
            logger.debug(
                "briefing.judge_context_failed", user_id=user_id, error=str(exc)
            )
            return ""

    async def _build_risk_context(self, user_id: str) -> str:
        if self._portfolio_risk_narrator is None:
            return ""
        try:
            result = await self._portfolio_risk_narrator.narrate(user_id)
            return str(result) if result else ""
        except Exception as exc:
            logger.debug(
                "briefing.risk_context_failed", user_id=user_id, error=str(exc)
            )
            return ""

    async def _build_next_action_context(self, user_id: str) -> str:
        if self._next_action_suggester is None:
            return ""
        try:
            result = await self._next_action_suggester.suggest(user_id)
            return str(result) if result else ""
        except Exception as exc:
            logger.debug(
                "briefing.next_action_context_failed", user_id=user_id, error=str(exc)
            )
            return ""

    async def _build_trend_prediction_context(self, user_id: str) -> str:
        if self._trend_prediction_store is None:
            return ""
        try:
            result = await self._trend_prediction_store.get_recent(user_id)
            return str(result) if result else ""
        except Exception as exc:
            logger.debug(
                "briefing.trend_prediction_context_failed",
                user_id=user_id,
                error=str(exc),
            )
            return ""

    async def _build_agenda_context(self, user_id: str) -> str:
        """Build agenda context from AgendaService (Wave B.2).

        Calls AgendaService.build_agenda(user_id) and formats the
        DailyAgendaResult (decide/watch/defer buckets) into a compact
        string injected into BriefingAgent context.

        Requires session to be set — skipped silently when session is None.
        Non-blocking.
        """
        if self._agenda_service is None or self._session is None:
            return ""
        try:
            result = await self._agenda_service.build_agenda(user_id)
            if result is None:
                return ""
            parts: list[str] = []
            decide = getattr(result, "decide", None)
            watch = getattr(result, "watch", None)
            defer = getattr(result, "defer", None)
            if decide:
                parts.append(f"DECIDE today: {', '.join(str(t) for t in decide)}")
            if watch:
                parts.append(f"WATCH: {', '.join(str(t) for t in watch)}")
            if defer:
                parts.append(f"DEFER: {', '.join(str(t) for t in defer)}")
            return "\n".join(parts) if parts else ""
        except Exception as exc:
            logger.debug(
                "briefing.agenda_context_failed", user_id=user_id, error=str(exc)
            )
            return ""

    # ------------------------------------------------------------------
    # Snapshot persistence
    # ------------------------------------------------------------------

    async def _persist_snapshot(
        self,
        user_id: str,
        phase: str,
        brief_text: str,
        tickers: list[str],
    ) -> int | None:
        if self._repo is None or self._session is None:
            return None
        try:
            snapshot = BriefSnapshot(
                user_id=user_id,
                phase=phase,
                brief_text=brief_text,
                tickers=tickers,
            )
            saved = await self._repo.save(snapshot)
            await self._session.commit()
            logger.info(
                "briefing.snapshot_persisted",
                snapshot_id=saved.id,
                user_id=user_id,
                phase=phase,
            )
            return saved.id
        except Exception as exc:
            logger.warning(
                "briefing.snapshot_persist_failed",
                user_id=user_id,
                phase=phase,
                error=str(exc),
            )
            await self._session.rollback()
            return None

    # ------------------------------------------------------------------
    # Quote formatting helper
    # ------------------------------------------------------------------

    @staticmethod
    def _format_quotes(quotes: dict[str, Any]) -> str:
        """Format a ticker→quote dict into a compact AI-friendly string."""
        if not quotes:
            return ""
        lines = []
        for ticker, q in quotes.items():
            if q is None:
                lines.append(f"{ticker}: no data")
                continue
            price = getattr(q, "price", None) or q.get("price") if hasattr(q, "get") else getattr(q, "price", None)
            change_pct = getattr(q, "change_pct", None) or (q.get("change_pct") if hasattr(q, "get") else None)
            volume = getattr(q, "volume", None) or (q.get("volume") if hasattr(q, "get") else None)
            parts = [ticker]
            if price is not None:
                parts.append(f"{price:,.0f}")
            if change_pct is not None:
                sign = "+" if change_pct >= 0 else ""
                parts.append(f"{sign}{change_pct:.2f}%")
            if volume is not None:
                parts.append(f"vol={volume:,.0f}")
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    async def _build_feedback_context(self, user_id: str) -> str:
        """Build feedback calibration context for the AI agent.

        Reads two independent signals and combines them:

        Signal 1: acted_rate_30d from DashboardService.get_brief_feedback_summary().
           Reads aggregate acted_rate_30d from DashboardService.get_brief_feedback_summary().
           When the sample is >= 10 feedbacks, injects a calibration hint:
           "Feedback calibration: acted_rate=42% (n=15). Adjust action specificity..."
           Below 10 feedbacks the sample is too small to be meaningful — skipped.

        Signal 2: recently acted tickers (Wave B.1.1 connector).
           Reads DashboardService.get_acted_tickers_recent(days=3) — tickers from
           briefs the user acted on in the last 3 days (cached 60s in readmodel).
           When found, appends a compact "Recently acted: VHM, TCB" line so
           BriefingAgent knows which positions already have committed actions —
           preventing redundant ACT_TODAY signals and surfacing other tickers.
           Requires minimum 1 acted ticker to inject.

        Either signal is independently useful — the block is non-empty when at
        least one of them produces output.

        Returns "" when:
          - session is None (scheduler, tests without DB)
          - both signals produce no output (sample < 10 AND no acted tickers)
          - any error occurs
        Non-blocking.
        """
        if self._session is None:
            return ""
        try:
            from src.readmodel.dashboard_service import DashboardService  # noqa: PLC0415

            dashboard = DashboardService(self._session)

            # Signal 1: acted_rate from feedback summary.
            summary = await dashboard.get_brief_feedback_summary(user_id=user_id)
            acted_rate = summary.get("acted_rate_30d") if summary else None
            total_feedbacks = summary.get("total_feedbacks_30d", 0) if summary else 0

            # Signal 2: recently acted tickers (Wave 2 connector).
            acted_tickers = await dashboard.get_acted_tickers_recent(
                user_id=user_id, days=3
            )

            parts: list[str] = []

            if total_feedbacks >= 10 and acted_rate is not None:
                parts.append(
                    f"Feedback calibration: acted_rate={acted_rate:.0%} "
                    f"(n={total_feedbacks}). "
                    "Adjust action specificity to match this investor's follow-through rate."
                )

            if acted_tickers:
                parts.append(
                    f"Recently acted (last 3d): {', '.join(acted_tickers)}. "
                    "Avoid redundant ACT_TODAY for these — focus on other watchlist tickers."
                )

            if not parts:
                return ""

            return "\n".join(parts)

        except Exception as exc:
            logger.debug(
                "briefing.feedback_context_failed",
                user_id=user_id,
                error=str(exc),
            )
            return ""

    async def record_feedback(
        self,
        brief_snapshot_id: int,
        user_id: str,
        outcome: str,
    ) -> None:
        """Persist user outcome feedback for a brief snapshot.

        Called by bot/commands/briefing.py when user clicks acted/watching/skipped.
        Outcome is one of BriefFeedbackOutcome.VALID_OUTCOMES:
          acted | watching | skipped

        Requires session to be set — raises RuntimeError otherwise.
        """
        if self._session is None or self._repo is None:
            raise RuntimeError("BriefingService.record_feedback requires a DB session")
        feedback = BriefFeedback(
            brief_snapshot_id=brief_snapshot_id,
            user_id=user_id,
            outcome=outcome,
        )
        await self._repo.save_feedback(feedback)
        await self._session.commit()
        logger.info(
            "briefing.feedback_recorded",
            snapshot_id=brief_snapshot_id,
            user_id=user_id,
            outcome=outcome,
        )
