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

from src.ai.agents.briefing import BriefingAgent
from src.ai.agents.thesis_judge import ThesisJudgeAgent
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
    """Return value of BriefingService.generate_*().

    snapshot_id  — persisted BriefSnapshot.id (used by bot to record feedback).
    text         — the full brief text to send to Discord.
    tickers      — tickers that appeared in context (for downstream use).
    """

    snapshot_id: int | None
    text: str
    tickers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class BriefingService:
    """Orchestrates morning and end-of-day brief generation.

    Constructor arguments
    ---------------------
      session            — AsyncSession (transaction owner is the caller).
      watchlist_service  — WatchlistService (get tickers).
      briefing_agent     — the AI agent that generates the brief text.
      quote_service      — QuoteService (price/volume context).
      pnl_service        — PnLService (unrealised P&L context).
      thesis_service     — ThesisService (thesis health context).
      sector_agent       — SectorRotationAgent (sector flow context).
      thesis_judge_agent       — thesis scoring context.
      risk_narrator      — PortfolioRiskNarrator (risk narrative).
      next_action_suggester — NextActionSuggester (next actions).
      trend_pred_store   — TrendPredictionStore (trend predictions).
      dashboard_service  — DashboardService (feedback calibration).
      agenda_service     — AgendaService (decide/watch/defer buckets).
                                 compact string injected into BriefingAgent context.
                                 Requires session to be set — skipped silently when
                                 session is None. Non-blocking.
                                 Pass None (default) to skip — preserves existing behavior.
    """

    def __init__(
        self,
        session: AsyncSession,
        watchlist_service: WatchlistService,
        briefing_agent: BriefingAgent,
        quote_service: Any = None,
        pnl_service: Any = None,
        thesis_service: Any = None,
        sector_agent: Any = None,
        thesis_judge_agent: ThesisJudgeAgent | None = None,
        risk_narrator: Any = None,
        next_action_suggester: Any = None,
        trend_pred_store: Any = None,
        dashboard_service: Any = None,
        agenda_service: Any = None,
    ) -> None:
        self._session = session
        self._watchlist_service = watchlist_service
        self._agent = briefing_agent
        self._quote_service = quote_service
        self._pnl_service = pnl_service
        self._thesis_service = thesis_service
        self._sector_agent = sector_agent
        self._thesis_judge_agent = thesis_judge_agent
        self._risk_narrator = risk_narrator
        self._next_action_suggester = next_action_suggester
        self._trend_pred_store = trend_pred_store
        self._dashboard_service = dashboard_service
        self._agenda_service = agenda_service
        self._repo = BriefSnapshotRepository(session)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_morning_brief(self, user_id: str) -> BriefResult:
        """Generate morning brief for user_id."""
        tickers, contexts = await self._collect_contexts(user_id)
        brief_text = await self._agent.generate_morning_brief(
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
        brief_text = await self._agent.generate_eod_brief(
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

    async def record_feedback(
        self,
        brief_snapshot_id: int,
        user_id: str,
        outcome: str,
        ticker: str | None = None,
        action_taken: str | None = None,
    ) -> None:
        """Record user feedback for a brief snapshot."""
        feedback = BriefFeedback(
            brief_snapshot_id=brief_snapshot_id,
            user_id=user_id,
            outcome=outcome,
            ticker=ticker,
            action_taken=action_taken,
        )
        self._session.add(feedback)
        logger.info(
            "briefing.feedback_recorded",
            snapshot_id=brief_snapshot_id,
            user_id=user_id,
            outcome=outcome,
        )

    # ------------------------------------------------------------------
    # Context collection
    # ------------------------------------------------------------------

    async def _collect_contexts(
        self, user_id: str
    ) -> tuple[list[str], dict[str, Any]]:
        """Collect all context sources in parallel.  Returns (tickers, contexts)."""
        tickers = await self._get_tickers(user_id)

        (
            quote_context,
            pnl_context,
            thesis_context,
            sector_context,
            judge_context,
            risk_context,
            next_action_context,
            trend_pred_context,
            feedback_context,
            agenda_context,
        ) = await asyncio.gather(
            self._build_quote_context(tickers),
            self._build_pnl_context(user_id),
            self._build_thesis_context(user_id),
            self._build_sector_context(tickers),
            self._build_judge_context(user_id),
            self._build_risk_context(user_id),
            self._build_next_action_context(user_id),
            self._build_trend_pred_context(user_id),
            self._build_feedback_context(user_id),
            self._build_agenda_context(user_id),
            return_exceptions=False,
        )

        contexts = {
            "quote_context": quote_context,
            "pnl_context": pnl_context,
            "thesis_context": thesis_context,
            "sector_context": sector_context,
            "judge_context": judge_context,
            "risk_context": risk_context,
            "next_action_context": next_action_context,
            "trend_pred_context": trend_pred_context,
            "feedback_context": feedback_context,
            "agenda_context": agenda_context,
        }
        return tickers, contexts

    async def _get_tickers(self, user_id: str) -> list[str]:
        try:
            items = await self._watchlist_service.get_items(user_id)
            return [item.ticker for item in items]
        except Exception as exc:
            logger.warning("briefing.get_tickers.failed", error=str(exc))
            return []

    async def _build_quote_context(self, tickers: list[str]) -> str:
        if not self._quote_service or not tickers:
            return ""
        try:
            quotes = await self._quote_service.batch_get_quotes(tickers)
            if not quotes:
                return ""
            lines = []
            for ticker, q in quotes.items():
                price = getattr(q, "close", None) or getattr(q, "price", None)
                change = getattr(q, "change_pct", None)
                vol = getattr(q, "volume", None)
                parts = [f"{ticker}: {price}"]
                if change is not None:
                    parts.append(f"{change:+.1f}%")
                if vol is not None:
                    parts.append(f"vol={vol:,.0f}")
                lines.append(" | ".join(parts))
            return "Quotes:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.quote_context.failed", error=str(exc))
            return ""

    async def _build_pnl_context(self, user_id: str) -> str:
        if not self._pnl_service:
            return ""
        try:
            pnl = await self._pnl_service.get_portfolio_pnl(user_id)
            if not pnl:
                return ""
            lines = []
            for ticker, data in pnl.items():
                pct = data.get("unrealised_pct", 0)
                lines.append(f"{ticker}: {pct:+.1f}%")
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
            health_items = await self._thesis_service.get_thesis_health(user_id)
            if not health_items:
                return ""

            lines = []
            for item in health_items:
                ticker = item.get("ticker", "?")
                entry = item.get("entry_thesis", "")
                target = item.get("target_price")
                stop = item.get("stop_loss")
                days_since = item.get("days_since_review")
                assumption_count = item.get("assumption_count", 0)

                parts = [f"{ticker}: {entry[:80]}"]
                if target:
                    parts.append(f"target={target}")
                if stop:
                    parts.append(f"stop={stop}")
                if days_since is not None:
                    parts.append(f"last_review={days_since}d ago")
                if assumption_count:
                    parts.append(f"assumptions={assumption_count}")

                lines.append(" | ".join(parts))

            return "Active theses:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.thesis_context.failed", error=str(exc))
            return ""

    async def _build_sector_context(self, tickers: list[str]) -> str:
        if not self._sector_agent or not tickers:
            return ""
        try:
            result = await self._sector_agent.analyse(tickers)
            if not result:
                return ""
            if isinstance(result, str):
                return f"Sector flow:\n{result}"
            return f"Sector flow:\n{result}"
        except Exception as exc:
            logger.warning("briefing.sector_context.failed", error=str(exc))
            return ""

    async def _build_judge_context(self, user_id: str) -> str:
        """Build thesis judge context string injected into BriefingAgent context.

        Calls ThesisJudgeAgent.judge(theses) → thesis scores.
        Returns empty string if agent is not available or no active theses.
        """
        if self._thesis_judge_agent is None or self._thesis_service is None:
            return ""
        try:
            theses = await self._thesis_service.list_active(user_id=user_id)
            if not theses:
                return ""
            result = await self._thesis_judge_agent.judge(theses)
            if not result:
                return ""
            if isinstance(result, str):
                return f"Thesis scores:\n{result}"
            lines = []
            for ticker, score in result.items():
                lines.append(f"{ticker}: {score}")
            return "Thesis scores:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.judge_context.failed", error=str(exc))
            return ""

    async def _build_risk_context(self, user_id: str) -> str:
        if not self._risk_narrator:
            return ""
        try:
            result = await self._risk_narrator.narrate(user_id)
            if not result:
                return ""
            return f"Risk:\n{result}"
        except Exception as exc:
            logger.warning("briefing.risk_context.failed", error=str(exc))
            return ""

    async def _build_next_action_context(self, user_id: str) -> str:
        if not self._next_action_suggester:
            return ""
        try:
            result = await self._next_action_suggester.suggest(user_id)
            if not result:
                return ""
            return f"Next actions:\n{result}"
        except Exception as exc:
            logger.warning("briefing.next_action_context.failed", error=str(exc))
            return ""

    async def _build_trend_pred_context(self, user_id: str) -> str:
        if not self._trend_pred_store:
            return ""
        try:
            result = await self._trend_pred_store.get_recent(user_id)
            if not result:
                return ""
            return f"Trend predictions:\n{result}"
        except Exception as exc:
            logger.warning("briefing.trend_pred_context.failed", error=str(exc))
            return ""

    async def _build_feedback_context(self, user_id: str) -> str:
        """Build feedback calibration context.

        Wave B.1.1: Also injects recently-acted tickers from DashboardService
        to prevent redundant ACT_TODAY suggestions for already-acted positions.
        """
        parts: list[str] = []

        if self._dashboard_service:
            try:
                summary = await self._dashboard_service.get_brief_feedback_summary()
                if summary:
                    parts.append(f"Feedback calibration:\n{summary}")
            except Exception as exc:
                logger.warning(
                    "briefing.feedback_context.summary_failed", error=str(exc)
                )

            try:
                acted = await self._dashboard_service.get_acted_tickers_recent(
                    user_id=user_id
                )
                if acted:
                    tickers_str = ", ".join(acted)
                    parts.append(f"Recently acted tickers: {tickers_str}")
            except Exception as exc:
                logger.warning(
                    "briefing.feedback_context.acted_tickers_failed", error=str(exc)
                )

        return "\n".join(parts)

    async def _build_agenda_context(self, user_id: str) -> str:
        """Wave B.2: build decide/watch/defer agenda context.

        Calls AgendaService.build_agenda(user_id) → DailyAgendaResult.
        Returns empty string if agenda_service is None or on any error.
        """
        if not self._agenda_service:
            return ""
        try:
            agenda = await self._agenda_service.build_agenda(user_id)
            if not agenda:
                return ""
            lines: list[str] = ["Daily agenda:"]
            decide = getattr(agenda, "decide", []) or []
            watch = getattr(agenda, "watch", []) or []
            defer = getattr(agenda, "defer", []) or []
            if decide:
                lines.append("  DECIDE: " + ", ".join(str(t) for t in decide))
            if watch:
                lines.append("  WATCH:  " + ", ".join(str(t) for t in watch))
            if defer:
                lines.append("  DEFER:  " + ", ".join(str(t) for t in defer))
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.agenda_context.failed", error=str(exc))
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
        try:
            snapshot = BriefSnapshot(
                user_id=user_id,
                brief_type=brief_type,
                brief_text=brief_text,
                tickers=tickers,
            )
            self._session.add(snapshot)
            await self._session.flush()
            logger.info(
                "briefing.snapshot_persisted",
                snapshot_id=snapshot.id,
                brief_type=brief_type,
                user_id=user_id,
            )
            return snapshot.id
        except Exception as exc:
            logger.warning("briefing.persist_snapshot.failed", error=str(exc))
            return None
