"""Briefing service — owner of brief generation flow.

Owner: briefing segment.

Responsibilities:
- collect watchlist tickers from watchlist segment
- collect market context from market segment (quotes)
- collect portfolio P&L snapshot from portfolio segment (optional)
- collect active thesis context from thesis segment (optional)
- collect past decision lessons from thesis segment (optional, via LessonService)
- collect investor profile context from ai segment (optional, via ContextBuilder)
- collect sector rotation signal from ai segment (optional, via SectorRotationAgent)
- collect thesis judge verdicts from ai segment (optional, via ThesisJudgeAgent)
- collect brief feedback summary from readmodel segment (optional, via DashboardService)
- call BriefingAgent for morning/EOD narrative
- attach PortfolioRiskNarrativeOutput via PortfolioRiskNarratorAgent (optional)
- attach NextActionPlan via NextActionSuggester (optional)
- attach TrendPredictions via TrendPredictionStore (optional)
- persist BriefSnapshot via BriefSnapshotRepository
- return BriefResult(output, snapshot_id) to adapters
- record user feedback (acted/watching/skipped) via record_feedback()

Non-responsibilities:
- no Discord formatting (see formatter.py)
- no HTTP route logic
- no scheduler logic

Context dedup rule (Wave 1):
  When ContextBuilder successfully produces a non-empty investor_profile block,
  that block already contains thesis health, portfolio bias, and recent lessons.
  In that case, the individual thesis_context / portfolio_context / past_lessons
  strings are zeroed out before being passed to the agent so each fact reaches
  the AI exactly once. The individual _build_* methods are kept intact as
  fallback for when session is None (scheduler, tests without DB).

Feedback calibration (Wave 3):
  _build_feedback_context() reads acted_rate from readmodel (lazy import).
  Requires minimum 10 feedback samples to inject — below that threshold the
  block is empty and brief generation is unaffected. The feedback string
  instructs the AI to adjust action count/specificity but never overrides
  risk_appetite from investor_profile.

Thesis Judge integration (Wave 2):
  When thesis_judge_agent is injected, _build_thesis_judge_block() runs
  ThesisJudgeAgent.run_batch() against all active theses before BriefingAgent
  is called. Only non-ON_TRACK verdicts (weakening / invalidated / review_now)
  are appended to market_context so the brief narrative can reference structured
  verdict data rather than inferring thesis health from raw text.
  Non-blocking: any failure returns ("", False, {}) and brief is unaffected.

Last-review continuity (P4):
  _build_thesis_judge_block() now calls _fetch_last_review_summary() for each
  thesis before building the trigger dict. When a ThesisReview row exists,
  a compact summary (verdict, confidence, action, key risks, date) is injected
  into signal_context["last_review_summary"] so ThesisJudgeAgent always knows
  what was said last time — preventing contradictory verdicts without reasoning.
  _fetch_last_review_summary() is non-blocking: DB errors return None silently.

Conviction history (P4 wave 2):
  _build_thesis_judge_block() now also calls _fetch_conviction_history() for
  each thesis. The last 4 ThesisReview rows (excluding the most recent, which
  is already captured in last_review_summary) are mapped to a compact
  list[dict] with date/verdict/confidence and passed as conviction_history in
  the trigger dict. This gives ThesisJudgeAgent a longitudinal view of how
  conviction has evolved, enabling it to detect drift, reversals, and
  accumulating weakness rather than judging each run in isolation.

Perf fixes (B1/B3/B4):
  B1: _build_market_context accepts optional pre-fetched quotes dict — avoids
      second get_bulk_quotes() call when invoked from _build_market_context_with_judge.
  B3: _build_thesis_judge_block uses ThesisRepository.list_reviews_batch() —
      single IN-query replaces N×2 sequential per-thesis DB calls.
  B4: _collect_contexts caches active theses list and passes it into both
      _build_thesis_context and _build_thesis_judge_block to avoid calling
      list_for_user(active) twice.

B2 fix:
  thesis_judge_ran now reflects whether ThesisJudgeAgent.run_batch() was
  actually called, not merely whether thesis_judge_agent was injected.
  _build_thesis_judge_block returns tuple[str, bool] (block, did_run).
  did_run=True only when run_batch() was reached (active theses existed and
  no early-exit occurred). _build_market_context_with_judge propagates the
  tuple; _collect_contexts unpacks it into thesis_judge_ran.

Wave 1 fixes:
  - record_feedback(): outcome validated against BriefFeedbackOutcome.VALID_OUTCOMES
    before DB insert; invalid values are rejected with a warning log (non-raising).
  - _build_market_context(): q.close accessed via getattr with .price fallback
    to guard against quote struct mismatch between B1 pre-fetched path and
    get_bulk_quotes() path. change_pct and volume also use getattr consistently.

PortfolioRiskNarrator (Wave 3):
  When portfolio_risk_narrator is injected, _run_portfolio_risk_narrator() is
  called after BriefingAgent returns. It builds PortfolioRiskNarratorContext
  from pnl_service + SignalEngine output (via ctx quotes) and attaches the
  result to BriefOutput.portfolio_narrative. Fully non-blocking: any failure
  leaves portfolio_narrative=None and the brief is unaffected.

PortfolioRiskNarrator bug fixes:
  B1: PortfolioRiskNote was constructed with 5 non-existent fields. Fixed to
      use correct contract from _base.py: position_count, total_pnl_pct,
      top_concentration, losing_positions, misaligned_positions.
  B2: _portfolio_risk_narrator.run() does not exist. Fixed to .narrate().
  Also: PortfolioRiskNarratorContext kwargs corrected to match dataclass fields
      (portfolio_note=, ranked_signals=[], risk_alerts=[], stress_impact_note="").

NextActionSuggester (post-brief synthesis):
  When next_action_suggester is injected, _run_next_action_suggester() is
  called after _run_portfolio_risk_narrator(). It builds per-ticker signal
  contexts from ctx["tickers"] + ctx["quotes"] + result.ticker_summaries
  and calls NextActionSuggester.suggest(contexts). The returned NextActionPlan
  is attached to BriefOutput.next_action_plan. Fully non-blocking: any failure
  leaves next_action_plan=None and the brief is unaffected.
  NextActionSuggester has built-in fallback (rule-based, confidence=0.3) so
  AI errors are already handled inside the agent — the outer try/except here
  guards against unexpected structural failures only.

TrendPredictionStore (post-brief enrichment):
  When trend_prediction_store is injected, _run_trend_predictions() is called
  after _run_next_action_suggester(). It fetches precomputed TrendPrediction
  objects for the watchlist tickers via store.get_for_tickers(tickers) and
  attaches the result to BriefOutput.trend_predictions. Fully non-blocking:
  any failure leaves trend_predictions=None and the brief is unaffected.
  Store must implement async get_for_tickers(tickers: list[str]) -> list[Any].

Wave 1 — judge verdicts forward:
  _build_thesis_judge_block now returns tuple[str, bool, dict[str, str]].
  The third element is verdicts_by_ticker: {ticker: verdict_str} containing
  only non-ON_TRACK verdicts. _build_market_context_with_judge propagates
  this as tuple[str, bool, dict]. _collect_contexts unpacks it into the
  ctx["judge_verdicts"] key (always a dict, {} when judge did not run).
  _run_next_action_suggester prefers judge_verdicts.get(ticker) over the
  BriefingAgent.signal proxy when a structured verdict is available.
  Falls back to signal proxy when no judge verdict exists for a ticker.
  Fully backward compatible — no interface changes outside this file.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.briefing import BriefingAgent
from src.ai.agents.thesis_judge import ThesisJudgeAgent
from src.ai.context_builder import ContextBuilder, render_for_agent
from src.ai.schemas import BriefOutput
from src.briefing.models import BriefFeedback, BriefFeedbackOutcome, BriefSnapshot
from src.briefing.repository import BriefSnapshotRepository
from src.market.registry import registry as symbol_registry
from src.platform.logging import get_logger
from src.thesis.lesson_service import LessonService
from src.watchlist.service import WatchlistService

logger = get_logger(__name__)


@dataclass
class BriefResult:
    """Return value of generate_morning_brief / generate_eod_brief.

    Keeps BriefOutput and the persisted snapshot_id together so bot adapters
    can attach feedback buttons without a second DB query.

    snapshot_id is None when persistence was skipped (session=None or flush
    failed — brief was still delivered, just not persisted).
    """

    output: BriefOutput
    snapshot_id: int | None


class BriefingService:
    """Orchestrates morning and end-of-day brief generation.

    Args:
        watchlist_service:       reads user watchlist tickers.
        quote_service:           fetches bulk market quotes.
        briefing_agent:          AI agent that writes the brief narrative.
        pnl_service:             optional — reads open position P&L for portfolio context.
                                 Pass None to skip portfolio section gracefully.
        thesis_service:          optional — reads active theses for thesis context injection.
                                 When provided, stop_loss levels and key assumptions are
                                 formatted and sent to the AI so it can force ACT_TODAY
                                 for any ticker approaching invalidation.
                                 Pass None to skip thesis section gracefully.
        session:                 AsyncSession for persisting BriefSnapshot, reading past
                                 decision lessons via LessonService, building investor
                                 profile context via ContextBuilder, and reading feedback
                                 summary via DashboardService (Wave 3).
                                 Pass None to skip persistence, lesson injection,
                                 investor profile injection, and feedback injection.
        sector_rotation_agent:   optional — AI agent that detects sector divergence signals.
                                 When provided, its actionable_insight and top watchlist
                                 crosscheck items are appended to market_context so the
                                 BriefingAgent can factor in rotation dynamics.
                                 Pass None (default) to skip — preserves existing behavior.
        thesis_judge_agent:      optional — AI agent that cross-checks active theses against
                                 current market signals. When provided, verdicts (weakening /
                                 invalidated) are formatted and appended to market_context
                                 AFTER sector rotation block, BEFORE BriefingAgent LLM call.
                                 Only non-ON_TRACK verdicts are emitted to avoid noise.
                                 Pass None (default) to skip — preserves existing behavior.
        portfolio_risk_narrator: optional — AI agent that produces a structured portfolio
                                 risk narrative (PortfolioRiskNarrativeOutput). When provided,
                                 called after BriefingAgent returns; result attached to
                                 BriefOutput.portfolio_narrative. Requires pnl_service to be
                                 set — skipped silently when pnl_service is None.
                                 Pass None (default) to skip — preserves existing behavior.
        next_action_suggester:   optional — AI agent that synthesises per-ticker signal
                                 contexts into an ordered NextActionPlan. When provided,
                                 called after portfolio_risk_narrator; result attached to
                                 BriefOutput.next_action_plan. Skipped silently when
                                 tickers list is empty.
                                 Pass None (default) to skip — preserves existing behavior.
        trend_prediction_store:  optional — store chứa TrendPrediction đã được precomputed
                                 bởi TrendEngine. Khi được inject, _run_trend_predictions()
                                 fetch top predictions cho watchlist tickers và attach vào
                                 BriefOutput.trend_predictions. Fully non-blocking.
                                 Store phải implement: async get_for_tickers(tickers) -> list.
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

    async def generate_morning_brief(self, user_id: str) -> BriefResult:
        ctx = await self._collect_contexts(user_id, phase="morning")
        logger.info(
            "briefing.generate_morning",
            user_id=user_id,
            tickers=ctx["tickers"],
            has_portfolio=bool(ctx["portfolio_context"]),
            has_thesis=bool(ctx["thesis_context"]),
            has_lessons=bool(ctx["past_lessons"]),
            has_investor_profile=bool(ctx["investor_profile"]),
            has_sector_rotation=bool(ctx["sector_rotation_injected"]),
            has_feedback_summary=bool(ctx["feedback_summary"]),
            thesis_judge_ran=ctx["thesis_judge_ran"],
            judge_verdict_count=len(ctx["judge_verdicts"]),
            context_source=ctx["context_source"],
        )
        result = await self._agent.morning_brief(
            market_context=ctx["market_context"],
            watchlist_tickers=ctx["tickers"],
            portfolio_context=ctx["portfolio_context"],
            thesis_context=ctx["thesis_context"],
            past_lessons=ctx["past_lessons"],
            investor_profile=ctx["investor_profile"],
            feedback_summary=ctx["feedback_summary"],
        )
        # Wire PortfolioRiskNarratorAgent — attaches to result.portfolio_narrative
        result = await self._run_portfolio_risk_narrator(user_id=user_id, result=result, ctx=ctx)
        # Wire NextActionSuggester — attaches to result.next_action_plan
        result = await self._run_next_action_suggester(user_id=user_id, result=result, ctx=ctx)
        # Wire TrendPredictionStore — attaches to result.trend_predictions
        result = await self._run_trend_predictions(user_id=user_id, result=result, ctx=ctx)
        logger.info(
            "briefing.morning_enrichment",
            user_id=user_id,
            has_portfolio_narrative=result.portfolio_narrative is not None,
            has_next_action_plan=result.next_action_plan is not None,
            has_trend_predictions=result.trend_predictions is not None,
            next_action_critical_count=(
                result.next_action_plan.total_critical
                if result.next_action_plan is not None
                else 0
            ),
        )
        snapshot_id = await self._persist(
            user_id=user_id, phase="morning", output=result, tickers=ctx["tickers"]
        )
        return BriefResult(output=result, snapshot_id=snapshot_id)

    async def generate_eod_brief(self, user_id: str) -> BriefResult:
        ctx = await self._collect_contexts(user_id, phase="eod")
        logger.info(
            "briefing.generate_eod",
            user_id=user_id,
            tickers=ctx["tickers"],
            has_portfolio=bool(ctx["portfolio_context"]),
            has_thesis=bool(ctx["thesis_context"]),
            has_lessons=bool(ctx["past_lessons"]),
            has_investor_profile=bool(ctx["investor_profile"]),
            has_sector_rotation=bool(ctx["sector_rotation_injected"]),
            has_feedback_summary=bool(ctx["feedback_summary"]),
            thesis_judge_ran=ctx["thesis_judge_ran"],
            judge_verdict_count=len(ctx["judge_verdicts"]),
            context_source=ctx["context_source"],
        )
        result = await self._agent.eod_brief(
            market_context=ctx["market_context"],
            watchlist_tickers=ctx["tickers"],
            portfolio_context=ctx["portfolio_context"],
            thesis_context=ctx["thesis_context"],
            past_lessons=ctx["past_lessons"],
            investor_profile=ctx["investor_profile"],
            feedback_summary=ctx["feedback_summary"],
        )
        # Wire PortfolioRiskNarratorAgent — attaches to result.portfolio_narrative
        result = await self._run_portfolio_risk_narrator(user_id=user_id, result=result, ctx=ctx)
        # Wire NextActionSuggester — attaches to result.next_action_plan
        result = await self._run_next_action_suggester(user_id=user_id, result=result, ctx=ctx)
        # Wire TrendPredictionStore — attaches to result.trend_predictions
        result = await self._run_trend_predictions(user_id=user_id, result=result, ctx=ctx)
        logger.info(
            "briefing.eod_enrichment",
            user_id=user_id,
            has_portfolio_narrative=result.portfolio_narrative is not None,
            has_next_action_plan=result.next_action_plan is not None,
            has_trend_predictions=result.trend_predictions is not None,
            next_action_critical_count=(
                result.next_action_plan.total_critical
                if result.next_action_plan is not None
                else 0
            ),
        )
        snapshot_id = await self._persist(
            user_id=user_id, phase="eod", output=result, tickers=ctx["tickers"]
        )
        return BriefResult(output=result, snapshot_id=snapshot_id)

    async def record_feedback(
        self,
        brief_snapshot_id: int,
        user_id: str,
        outcome: str,
    ) -> None:
        """Persist a user feedback row for a brief snapshot.

        outcome must be one of BriefFeedbackOutcome.VALID_OUTCOMES:
        "acted" | "watching" | "skipped".
        Invalid values are rejected with a warning log and early return —
        non-raising so Discord interaction handlers are never blocked.
        Append-only — does not overwrite previous feedback rows.
        DB errors are logged and swallowed.
        """
        if self._session is None:
            logger.warning(
                "briefing.record_feedback.no_session",
                brief_snapshot_id=brief_snapshot_id,
                user_id=user_id,
                outcome=outcome,
            )
            return

        # Wave 1: validate outcome before touching the DB.
        if outcome not in BriefFeedbackOutcome.VALID_OUTCOMES:
            logger.warning(
                "briefing.record_feedback.invalid_outcome",
                brief_snapshot_id=brief_snapshot_id,
                user_id=user_id,
                outcome=outcome,
                valid_outcomes=sorted(BriefFeedbackOutcome.VALID_OUTCOMES),
            )
            return

        try:
            feedback = BriefFeedback(
                brief_snapshot_id=brief_snapshot_id,
                user_id=user_id,
                outcome=outcome,
            )
            self._session.add(feedback)
            await self._session.flush()
            # Do NOT call self._session.commit() here — session is injected
            # by the caller (FastAPI dependency / bot handler) which owns the
            # transaction lifecycle. Calling commit() on an injected session
            # would prematurely commit unrelated pending work from the outer
            # request scope. Callers must commit after this method returns.
            logger.info(
                "briefing.feedback_saved",
                feedback_id=feedback.id,
                brief_snapshot_id=brief_snapshot_id,
                user_id=user_id,
                outcome=outcome,
            )
        except Exception as exc:
            logger.error(
                "briefing.feedback_save_failed",
                brief_snapshot_id=brief_snapshot_id,
                user_id=user_id,
                outcome=outcome,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _collect_contexts(self, user_id: str, phase: str) -> dict:
        """Gather all context strings needed by morning and EOD brief generation.

        Dedup rule:
          ContextBuilder (via _build_investor_profile_context) already assembles
          thesis health, portfolio bias, and recent lessons into investor_profile.
          When investor_profile is non-empty we zero out the three overlapping
          individual context strings so the AI receives each fact exactly once.

          When session is None (scheduler, tests), investor_profile is always ""
          and the individual builders run as normal fallback.

        Feedback (Wave 3):
          _build_feedback_context() is independent of the dedup rule — it reads
          acted_rate from readmodel and is always attempted when session is set.
          Returns "" when sample < 10 or any error occurs.

        Thesis Judge (Wave 2):
          When thesis_judge_agent is injected, market_context is built via
          _build_market_context_with_judge() which appends structured ThesisJudge
          verdicts (non-ON_TRACK only) after the sector rotation block.
          Quotes are fetched once and reused for both market_context and judge block.

        B2 fix:
          thesis_judge_ran is set from the did_run bool returned by
          _build_market_context_with_judge (which in turn comes from
          _build_thesis_judge_block). It is True only when run_batch() was
          actually called — not merely when the agent was injected.

        B4 fix:
          Active theses are fetched once here and passed into both
          _build_thesis_context (fallback path) and _build_thesis_judge_block
          to avoid calling list_for_user(active) twice per brief.

        Judge verdicts forward (Wave 1):
          When thesis_judge_agent ran, ctx["judge_verdicts"] is populated with
          {ticker: verdict_str} for all non-ON_TRACK verdicts. This lets
          _run_next_action_suggester use precise structured verdicts instead of
          the BriefingAgent.signal proxy. Always {} when judge did not run.

        Returns a dict with keys:
          tickers, market_context, portfolio_context, thesis_context,
          past_lessons, investor_profile, feedback_summary,
          context_source, sector_rotation_injected, thesis_judge_ran,
          judge_verdicts, quotes (pre-fetched dict, may be {} when judge not injected).
        """
        t_total = time.monotonic()

        t0 = time.monotonic()
        tickers = await self._get_watchlist_tickers(user_id)
        watchlist_ms = round((time.monotonic() - t0) * 1000)

        # B4: fetch active theses once, reuse in both builder paths below.
        cached_theses: list | None = None
        if self._thesis_service is not None:
            try:
                cached_theses = await self._thesis_service.list_for_user(  # type: ignore[attr-defined]
                    user_id=user_id, status="active"
                )
            except Exception as exc:
                logger.warning("briefing.theses_prefetch_failed", user_id=user_id, error=str(exc))
                cached_theses = []

        t0 = time.monotonic()
        thesis_judge_ran = False
        judge_verdicts: dict[str, str] = {}
        # Pre-fetched quotes — populated when thesis_judge_agent is injected (B1);
        # kept in ctx so _run_portfolio_risk_narrator / _run_next_action_suggester
        # can reuse without a second fetch.
        _quotes_by_ticker: dict = {}
        if self._thesis_judge_agent is not None:
            # Fetch quotes once — reused by market_context, thesis_judge_block,
            # portfolio_risk_narrator, and next_action_suggester.
            try:
                _raw_quotes = await self._quote_service.get_bulk_quotes(tickers)  # type: ignore[attr-defined]
                _quotes_by_ticker = {q.ticker: q for q in _raw_quotes}
            except Exception:
                _quotes_by_ticker = {}
            # Unpack tuple[str, bool, dict[str, str]]
            market_context, thesis_judge_ran, judge_verdicts = (
                await self._build_market_context_with_judge(
                    user_id=user_id,
                    tickers=tickers,
                    phase=phase,
                    quotes=_quotes_by_ticker,
                    cached_theses=cached_theses,
                )
            )
        else:
            market_context = await self._build_market_context(tickers, phase=phase)
        market_context_ms = round((time.monotonic() - t0) * 1000)

        # Try ContextBuilder first — it aggregates thesis + portfolio + lessons
        t0 = time.monotonic()
        investor_profile = await self._build_investor_profile_context(user_id)
        context_builder_ms = round((time.monotonic() - t0) * 1000)

        individual_builders_ms: int | None = None
        if investor_profile:
            # ContextBuilder produced a full block — skip individual builders
            # to avoid sending the same facts twice to the AI.
            context_source = "context_builder"
            portfolio_context = ""
            thesis_context = ""
            past_lessons = ""
        else:
            # Fallback: session=None or ContextBuilder found no data.
            # Run individual builders so the brief is never empty-handed.
            context_source = "individual_builders"
            t0 = time.monotonic()
            portfolio_context = await self._build_portfolio_context(user_id)
            # B4: pass cached_theses to avoid a second list_for_user call.
            thesis_context = await self._build_thesis_context(user_id, theses=cached_theses)
            past_lessons = await self._build_lesson_context(user_id)
            individual_builders_ms = round((time.monotonic() - t0) * 1000)

        # Feedback summary — independent of dedup rule, always attempted.
        t0 = time.monotonic()
        feedback_summary = await self._build_feedback_context(user_id)
        feedback_ms = round((time.monotonic() - t0) * 1000)

        total_ms = round((time.monotonic() - t_total) * 1000)

        log_kwargs: dict = dict(
            user_id=user_id,
            phase=phase,
            context_source=context_source,
            ticker_count=len(tickers),
            watchlist_ms=watchlist_ms,
            market_context_ms=market_context_ms,
            context_builder_ms=context_builder_ms,
            feedback_ms=feedback_ms,
            total_ms=total_ms,
        )
        if individual_builders_ms is not None:
            log_kwargs["individual_builders_ms"] = individual_builders_ms

        logger.info("briefing.collect_contexts_complete", **log_kwargs)

        # Sector rotation block is already embedded inside market_context.
        # Use content-based detection instead of agent-presence proxy so the
        # flag accurately reflects whether a non-empty block was actually
        # appended — agent injected + tickers present does NOT guarantee a
        # non-empty block (agent may return empty or raise, both → "").
        sector_rotation_injected = "Sector Rotation Signal" in market_context

        return {
            "tickers": tickers,
            "market_context": market_context,
            "portfolio_context": portfolio_context,
            "thesis_context": thesis_context,
            "past_lessons": past_lessons,
            "investor_profile": investor_profile,
            "feedback_summary": feedback_summary,
            "context_source": context_source,
            "sector_rotation_injected": sector_rotation_injected,
            "thesis_judge_ran": thesis_judge_ran,
            "judge_verdicts": judge_verdicts,
            "quotes": _quotes_by_ticker,
        }

    async def _run_portfolio_risk_narrator(
        self,
        user_id: str,
        result: BriefOutput,
        ctx: dict,
    ) -> BriefOutput:
        """Run PortfolioRiskNarratorAgent and attach output to result.portfolio_narrative.

        Fully non-blocking: any failure returns result unchanged (portfolio_narrative=None).
        Skipped silently when portfolio_risk_narrator or pnl_service is not injected.

        Builds PortfolioRiskNarratorContext from:
          - pnl_service.get_portfolio_pnl(user_id) → PortfolioRiskNote
          - ctx["quotes"] → per-ticker price data for concentration/loss derivation

        PortfolioRiskNote field contract (from _base.py):
          position_count, total_pnl_pct, top_concentration, losing_positions,
          misaligned_positions. Fields total_positions / total_market_value /
          total_unrealized_pnl / tickers do NOT exist on this model.

        Owner: briefing (adapter). Narrator logic stays in ai segment.
        """
        if self._portfolio_risk_narrator is None or self._pnl_service is None:
            return result
        try:
            from src.ai.agents.portfolio_risk_narrator import PortfolioRiskNarratorContext  # noqa: PLC0415
            from src.ai.schemas import PortfolioRiskNote  # noqa: PLC0415

            pnl = await self._pnl_service.get_portfolio_pnl(user_id)  # type: ignore[attr-defined]
            if not pnl or not getattr(pnl, "positions", None):
                return result

            positions = pnl.positions

            # Derive concentration / loss lists from position-level data.
            # weight_pct and unrealized_pct use getattr with safe defaults so
            # this path degrades gracefully when pnl model evolves.
            top_concentration = [
                p.ticker for p in positions
                if getattr(p, "weight_pct", 0.0) > 25.0
            ]
            losing_positions = [
                p.ticker for p in positions
                if getattr(p, "unrealized_pct", 0.0) < -5.0
            ]

            # Build PortfolioRiskNote with correct field contract from _base.py.
            risk_note = PortfolioRiskNote(
                position_count=len(positions),
                total_pnl_pct=getattr(pnl, "total_unrealized_pct", None),
                top_concentration=top_concentration,
                losing_positions=losing_positions,
                misaligned_positions=[],  # verdict data not available in briefing path
            )

            # PortfolioRiskNarratorContext dataclass fields:
            #   portfolio_note, ranked_signals, risk_alerts, stress_impact_note, portfolio_date
            narrator_ctx = PortfolioRiskNarratorContext(
                portfolio_note=risk_note,
                ranked_signals=[],       # SignalEngineOutput not available in briefing path
                risk_alerts=[],
                stress_impact_note="",
            )
            # Correct method name: .narrate() — not .run()
            narrative = await self._portfolio_risk_narrator.narrate(narrator_ctx)  # type: ignore[attr-defined]
            if narrative is not None:
                result.portfolio_narrative = narrative
        except Exception as exc:
            logger.warning(
                "briefing.portfolio_risk_narrator_failed",
                user_id=user_id,
                error=str(exc),
            )
        return result

    async def _run_next_action_suggester(
        self,
        user_id: str,
        result: BriefOutput,
        ctx: dict,
    ) -> BriefOutput:
        """Run NextActionSuggester and attach output to result.next_action_plan.

        Fully non-blocking: any failure returns result unchanged (next_action_plan=None).
        Skipped silently when next_action_suggester is not injected or tickers is empty.

        Builds per-ticker signal contexts from:
          - ctx["tickers"]              → one dict per ticker
          - ctx["quotes"]               → price change_pct as market signal hint
          - ctx["judge_verdicts"]       → structured ThesisJudge verdict per ticker
                                          (WEAKENING / INVALIDATED / REVIEW_NOW);
                                          takes precedence over BriefingAgent.signal proxy
          - result.ticker_summaries     → BriefingAgent signal field (bearish/bullish)
                                          used as watchdog_verdict proxy ONLY when no
                                          structured judge verdict is available for ticker

        Verdict priority (highest → lowest):
          1. judge_verdicts[ticker]  — structured, from ThesisJudgeAgent.run_batch()
          2. BriefingAgent.signal    — derived, bearish/bullish/neutral

        Owner: briefing (adapter). Suggester logic stays in ai segment.
        """
        if self._next_action_suggester is None:
            return result
        tickers = ctx.get("tickers", [])
        if not tickers:
            return result

        try:
            quotes: dict = ctx.get("quotes", {})
            judge_verdicts: dict[str, str] = ctx.get("judge_verdicts", {})

            # Build a lookup from ticker_summaries for signal + one_line
            summaries_by_ticker: dict = {}
            for ts in result.ticker_summaries:
                summaries_by_ticker[ts.ticker] = ts

            contexts: list[dict] = []
            for ticker in tickers:
                entry: dict = {"ticker": ticker}

                # Attach quote change_pct as a lightweight market signal.
                q = quotes.get(ticker)
                if q is not None:
                    change_pct = getattr(q, "change_pct", None)
                    if change_pct is not None:
                        entry["notes"] = f"change_pct={change_pct:+.2f}%"

                # Verdict priority:
                #   1. Structured ThesisJudge verdict (WEAKENING / INVALIDATED / REVIEW_NOW)
                #   2. BriefingAgent.signal proxy (bearish / bullish / neutral)
                judge_verdict = judge_verdicts.get(ticker)
                if judge_verdict is not None:
                    # Structured verdict available — use directly, uppercased for consistency.
                    entry["watchdog_verdict"] = judge_verdict.upper()
                else:
                    # Fall back to BriefingAgent signal proxy.
                    ts = summaries_by_ticker.get(ticker)
                    if ts is not None:
                        signal = getattr(ts, "signal", "neutral") or "neutral"
                        if signal.lower() == "bearish":
                            entry["watchdog_verdict"] = "BEARISH"
                        elif signal.lower() == "bullish":
                            entry["watchdog_verdict"] = "BULLISH"

                # one_line annotation from BriefingAgent (independent of verdict source).
                ts = summaries_by_ticker.get(ticker)
                if ts is not None:
                    one_line = getattr(ts, "one_line", "") or ""
                    if one_line:
                        entry["notes"] = (
                            entry.get("notes", "") + f" | {one_line}"
                        ).lstrip(" | ")

                contexts.append(entry)

            plan = await self._next_action_suggester.suggest(contexts)  # type: ignore[attr-defined]
            if plan is not None:
                result.next_action_plan = plan

        except Exception as exc:
            logger.warning(
                "briefing.next_action_suggester_failed",
                user_id=user_id,
                error=str(exc),
            )
        return result

    async def _run_trend_predictions(
        self,
        user_id: str,
        result: BriefOutput,
        ctx: dict,
    ) -> BriefOutput:
        """Fetch precomputed TrendPredictions and attach to result.trend_predictions.

        Fully non-blocking: any failure returns result unchanged (trend_predictions=None).
        Skipped silently when trend_prediction_store is not injected or tickers is empty.

        Reads from TrendPredictionStore.get_for_tickers(tickers) — expects a
        list[TrendPrediction]. Store must implement non-blocking fetch; staleness
        check (is_stale guard) is the store's responsibility, not this method's.

        Owner: briefing (adapter). Store + TrendPrediction schema live in
        readmodel (persist) or market (compute). This method is purely a wiring
        point — no business logic.
        """
        if self._trend_prediction_store is None:
            return result
        tickers = ctx.get("tickers", [])
        if not tickers:
            return result
        try:
            predictions = await self._trend_prediction_store.get_for_tickers(  # type: ignore[attr-defined]
                tickers=tickers
            )
            if predictions:
                result.trend_predictions = predictions
        except Exception as exc:
            logger.warning(
                "briefing.trend_predictions_failed",
                user_id=user_id,
                tickers=tickers,
                error=str(exc),
            )
        return result

    async def _persist(
        self,
        user_id: str,
        phase: str,
        output: BriefOutput,
        tickers: list[str],
    ) -> int | None:
        """Save a BriefSnapshot if a session was injected.

        Returns the snapshot id on success, None if persistence was skipped
        or failed. Failures are logged and swallowed so a DB error never
        blocks the brief delivery.
        """
        if self._repo is None:
            return None
        try:
            snapshot = BriefSnapshot(
                user_id=user_id,
                phase=phase,
                content=output.model_dump_json(),
                tickers=",".join(tickers) if tickers else None,
            )
            await self._repo.save(snapshot)
            logger.info(
                "briefing.snapshot_saved",
                user_id=user_id,
                phase=phase,
                snapshot_id=snapshot.id,
                ticker_count=len(tickers),
            )
            return snapshot.id
        except Exception as exc:
            logger.error(
                "briefing.snapshot_save_failed",
                user_id=user_id,
                phase=phase,
                error=str(exc),
            )
            return None

    async def _get_watchlist_tickers(self, user_id: str) -> list[str]:
        items = await self._watchlist_service.list_items(user_id=user_id)
        return [item.ticker for item in items]

    async def _build_market_context(
        self,
        tickers: list[str],
        phase: str,
        quotes: dict | None = None,
    ) -> str:
        """Build market context string from watchlist quotes.

        B1 fix: accepts optional pre-fetched quotes dict to skip a second
        get_bulk_quotes() call when invoked from _build_market_context_with_judge.
        When quotes is None (standalone callers, scheduler, tests), fetches as before.

        Wave 1 fix: q.close accessed via getattr with .price fallback to guard
        against struct mismatch between pre-fetched quotes (B1 path) and
        get_bulk_quotes() quote objects. Same guard applied to change_pct and volume.

        Args:
            tickers: watchlist tickers.
            phase:   "morning" | "eod".
            quotes:  optional pre-fetched {ticker: quote} dict. When provided,
                     get_bulk_quotes() is not called again.
        """
        now = datetime.now().strftime("%H:%M %d/%m/%Y")
        if not tickers:
            return (
                f"Thời điểm: {now}. Không có mã nào trong watchlist. "
                f"Hãy viết {phase} brief ở mức thị trường chung, nhấn mạnh quản trị rủi ro."
            )

        # Use pre-fetched quotes when available (B1); otherwise fetch.
        if quotes is not None:
            fetched_quotes = [quotes[t] for t in tickers if t in quotes]
        else:
            try:
                fetched_quotes = await self._quote_service.get_bulk_quotes(tickers)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning("briefing.quote_fetch_failed", tickers=tickers, error=str(exc))
                return (
                    f"Thời điểm: {now}. Không lấy được quote cho watchlist {', '.join(tickers)}. "
                    f"Hãy viết {phase} brief thận trọng, nêu rõ thiếu dữ liệu giá realtime."
                )

        lines = [f"Thời điểm: {now}. Watchlist: {', '.join(tickers)}."]
        for q in fetched_quotes:
            # Wave 1: guard .close with .price fallback — pre-fetched and
            # get_bulk_quotes() objects may use different attribute names.
            close_price = getattr(q, "close", None) or getattr(q, "price", 0.0) or 0.0
            parts = [f"{q.ticker}: {close_price:,.0f}"]
            change_pct = getattr(q, "change_pct", None)
            if change_pct is not None:
                parts.append(f"({change_pct:+.2f}%)")
            volume = getattr(q, "volume", None)
            if volume is not None:
                parts.append(f"vol={volume:,}")
            lines.append(" ".join(parts))

        # Sector rotation block — optional, non-blocking.
        rotation_block = await self._build_sector_rotation_block(tickers)
        if rotation_block:
            lines.append(rotation_block)

        return "\n".join(lines)

    async def _build_market_context_with_judge(
        self,
        user_id: str,
        tickers: list[str],
        phase: str,
        quotes: dict,
        cached_theses: list | None = None,
    ) -> tuple[str, bool, dict[str, str]]:
        """Build market context with thesis judge verdicts appended.

        B1 fix: passes pre-fetched quotes into _build_market_context so quotes
        are never fetched twice in the same brief generation cycle.

        B2 fix: returns tuple[str, bool, dict[str, str]] (market_context, did_run,
        verdicts_by_ticker). did_run reflects whether ThesisJudgeAgent.run_batch()
        was actually called (propagated from _build_thesis_judge_block). Callers
        must unpack the full tuple.

        B4 fix: accepts cached_theses from _collect_contexts to avoid a second
        list_for_user(active) call inside _build_thesis_judge_block.

        Wave 1: verdicts_by_ticker {ticker: verdict_str} forwarded from
        _build_thesis_judge_block so _run_next_action_suggester can use
        structured verdicts instead of the BriefingAgent.signal proxy.

        Non-blocking: falls back to (plain_market_context, False, {}) on any error.

        Args:
            user_id:        For loading active theses via thesis_service.
            tickers:        Watchlist tickers (already fetched).
            phase:          "morning" | "eod".
            quotes:         Pre-fetched quote objects keyed by ticker (may be {}).
            cached_theses:  Pre-fetched active theses list (may be None).

        Returns:
            (market_context_str, did_run, verdicts_by_ticker)
        """
        # B1: pass quotes so _build_market_context skips a second fetch.
        base = await self._build_market_context(tickers, phase=phase, quotes=quotes)
        judge_block, did_run, verdicts_by_ticker = await self._build_thesis_judge_block(
            user_id=user_id, quotes=quotes, cached_theses=cached_theses
        )
        if judge_block:
            return base + judge_block, did_run, verdicts_by_ticker
        return base, did_run, verdicts_by_ticker

    async def _fetch_reviews_batch_for_judge(
        self, thesis_ids: list[int]
    ) -> dict[int, list]:
        """Fetch up to 5 reviews per thesis in a single DB query (B3 fix).

        Returns {thesis_id: [ThesisReview, ...]} newest-first, capped at 5.
        Returns {} on empty input, session=None, or any DB error (non-blocking).

        Owner: briefing (adapter). Reads thesis segment DB via ThesisRepository.
        """
        if self._session is None or not thesis_ids:
            return {}
        try:
            from src.thesis.repository import ThesisRepository

            repo = ThesisRepository(self._session)
            return await repo.list_reviews_batch(thesis_ids, limit_per_thesis=5)
        except Exception as exc:
            logger.debug(
                "briefing.fetch_reviews_batch_failed",
                thesis_ids=thesis_ids,
                error=str(exc),
            )
            return {}

    def _format_last_review_summary(self, review: object) -> str | None:
        """Format a single ThesisReview into the compact anchor string for the Judge.

        Extracted from _fetch_last_review_summary to be reusable with batch-loaded rows.
        Returns None when review is None.
        """
        if review is None:
            return None
        reviewed_at = (
            review.reviewed_at.strftime("%d/%m/%Y")
            if review.reviewed_at
            else "?"
        )
        verdict = getattr(review, "verdict", "?")
        confidence = getattr(review, "confidence", None)
        confidence_str = f"{confidence:.2f}" if confidence is not None else "?"
        reasoning_raw = getattr(review, "reasoning", "") or ""
        reasoning_snippet = reasoning_raw[:120].rstrip()

        risk_signals_raw = getattr(review, "risk_signals", None)
        risk_signals: list[str] = []
        if risk_signals_raw:
            try:
                parsed = json.loads(risk_signals_raw)
                if isinstance(parsed, list):
                    risk_signals = [str(r) for r in parsed[:3]]
            except Exception:
                pass

        lines = [
            f"Review gần nhất ({reviewed_at}): verdict={verdict}, confidence={confidence_str}",
        ]
        if reasoning_snippet:
            lines.append(f"  reasoning: {reasoning_snippet}")
        if risk_signals:
            lines.append(f"  risk_signals: {'; '.join(risk_signals)}")
        lines.append(
            "⚠️ Nếu verdict thay đổi so với review này, phải giải thích rõ trigger trong reasoning."
        )
        return "\n".join(lines)

    def _format_conviction_history(self, reviews: list) -> list[dict] | None:
        """Convert a list of ThesisReview rows (rows 1-4, skipping index 0) into
        the compact conviction_history list[dict] expected by ThesisJudgeAgent.

        Extracted from _fetch_conviction_history to be reusable with batch-loaded rows.
        Returns None when history_rows is empty.
        """
        history_rows = reviews[1:]  # skip index 0 (already in last_review_summary)
        if not history_rows:
            return None
        history: list[dict] = []
        for r in history_rows:
            reviewed_at = (
                r.reviewed_at.strftime("%d/%m/%Y") if r.reviewed_at else "?"
            )
            confidence = getattr(r, "confidence", None)
            history.append({
                "date": reviewed_at,
                "verdict": str(getattr(r, "verdict", "?")),
                "confidence": round(confidence, 2) if confidence is not None else None,
            })
        return history

    async def _fetch_last_review_summary(self, thesis_id: int | str) -> str | None:
        """Fetch and format the latest ThesisReview for a thesis as a compact string.

        Used by _build_thesis_judge_block (single-thesis fallback path only —
        batch path uses _fetch_reviews_batch_for_judge + _format_last_review_summary).

        Returns None when session is None, no review exists, or any DB error.
        Non-blocking.
        """
        if self._session is None:
            return None
        try:
            from src.thesis.repository import ThesisRepository

            repo = ThesisRepository(self._session)
            thesis_id_int = int(thesis_id)
            review = await repo.get_latest_review(thesis_id_int)
            return self._format_last_review_summary(review)
        except Exception as exc:
            logger.debug(
                "briefing.fetch_last_review_summary_failed",
                thesis_id=thesis_id,
                error=str(exc),
            )
            return None

    async def _fetch_conviction_history(
        self, thesis_id: int | str
    ) -> list[dict] | None:
        """Fetch historical ThesisReview rows as a compact conviction timeline.

        Single-thesis fallback path only — batch path uses
        _fetch_reviews_batch_for_judge + _format_conviction_history.

        Returns None when session is None, fewer than 2 reviews, or any DB error.
        Non-blocking.
        """
        if self._session is None:
            return None
        try:
            from src.thesis.repository import ThesisRepository

            repo = ThesisRepository(self._session)
            thesis_id_int = int(thesis_id)
            reviews = await repo.list_reviews_by_thesis(thesis_id_int, limit=5)
            return self._format_conviction_history(reviews)
        except Exception as exc:
            logger.debug(
                "briefing.fetch_conviction_history_failed",
                thesis_id=thesis_id,
                error=str(exc),
            )
            return None

    async def _build_thesis_judge_block(
        self,
        user_id: str,
        quotes: dict,
        cached_theses: list | None = None,
    ) -> tuple[str, bool, dict[str, str]]:
        """Build thesis judge block and append to market context.

        Returns tuple[str, bool, dict[str, str]] (block_str, did_run, verdicts_by_ticker).
        verdicts_by_ticker contains {ticker: verdict_str} for all non-ON_TRACK verdicts
        so downstream consumers (_run_next_action_suggester) can use structured data
        instead of inferring verdict from the text block.
        did_run=True only when ThesisJudgeAgent.run_batch() was actually called.
        Non-blocking: returns ("", False, {}) on any error or early exit.
        """
        if self._thesis_judge_agent is None:
            return "", False, {}
        if self._thesis_service is None:
            return "", False, {}

        theses = cached_theses
        if theses is None:
            try:
                theses = await self._thesis_service.list_for_user(  # type: ignore[attr-defined]
                    user_id=user_id, status="active"
                )
            except Exception as exc:
                logger.warning(
                    "briefing.thesis_judge_block.theses_fetch_failed",
                    user_id=user_id,
                    error=str(exc),
                )
                return "", False, {}

        if not theses:
            return "", False, {}

        # B3: fetch all reviews in one batch query.
        thesis_ids = [int(t.id) for t in theses if getattr(t, "id", None) is not None]
        reviews_batch = await self._fetch_reviews_batch_for_judge(thesis_ids)

        signal_contexts = []
        for thesis in theses:
            ticker = getattr(thesis, "ticker", "") or ""
            thesis_id = getattr(thesis, "id", None)

            q = quotes.get(ticker)
            price = None
            change_pct = None
            if q is not None:
                price = getattr(q, "close", None) or getattr(q, "price", None)
                change_pct = getattr(q, "change_pct", None)

            # B3: use batch-loaded reviews instead of per-thesis DB calls.
            last_review_summary: str | None = None
            conviction_history: list[dict] | None = None
            if thesis_id is not None:
                thesis_id_int = int(thesis_id)
                rows = reviews_batch.get(thesis_id_int, [])
                if rows:
                    last_review_summary = self._format_last_review_summary(rows[0])
                    conviction_history = self._format_conviction_history(rows)

            signal_context: dict = {
                "ticker": ticker,
                "thesis_id": str(thesis_id) if thesis_id is not None else None,
                "thesis_title": getattr(thesis, "title", ""),
                "stop_loss": getattr(thesis, "stop_loss", None),
                "key_assumptions": getattr(thesis, "key_assumptions", []),
                "price": price,
                "change_pct": change_pct,
            }
            if last_review_summary:
                signal_context["last_review_summary"] = last_review_summary
            if conviction_history:
                signal_context["conviction_history"] = conviction_history

            signal_contexts.append(signal_context)

        try:
            verdicts = await self._thesis_judge_agent.run_batch(  # type: ignore[attr-defined]
                signal_contexts=signal_contexts
            )
        except Exception as exc:
            logger.warning(
                "briefing.thesis_judge_block.run_batch_failed",
                user_id=user_id,
                error=str(exc),
            )
            return "", False, {}

        # Filter to non-ON_TRACK verdicts only.
        actionable = [
            v for v in verdicts
            if getattr(v, "verdict", None) not in (None, "ON_TRACK")
        ]

        # Build verdicts_by_ticker dict for downstream consumers.
        verdicts_by_ticker: dict[str, str] = {
            getattr(v, "ticker", ""): str(getattr(v, "verdict", ""))
            for v in actionable
            if getattr(v, "ticker", "")
        }

        if not actionable:
            return "", True, verdicts_by_ticker

        lines = ["\n\n## Thesis Judge Verdicts"]
        for v in actionable:
            ticker = getattr(v, "ticker", "?")
            verdict = getattr(v, "verdict", "?")
            confidence = getattr(v, "confidence", None)
            action = getattr(v, "recommended_action", "")
            conviction_delta = getattr(v, "conviction_delta", None)
            reasoning = getattr(v, "reasoning", "") or ""
            reasoning_snippet = reasoning[:100].rstrip()

            parts = [f"- {ticker}: {verdict}"]
            if confidence is not None:
                parts.append(f"conf={confidence:.2f}")
            if conviction_delta is not None:
                parts.append(f"delta={conviction_delta:+.2f}")
            if action:
                parts.append(f"action={action}")
            if reasoning_snippet:
                parts.append(f"| {reasoning_snippet}")
            lines.append(" ".join(parts))

        return "\n".join(lines), True, verdicts_by_ticker

    async def _build_sector_rotation_block(self, tickers: list[str]) -> str:
        """Build sector rotation signal block.

        Returns a formatted string to append to market_context, or "" if
        sector_rotation_agent is not injected, tickers is empty, or any error.
        Non-blocking.
        """
        if self._sector_rotation_agent is None or not tickers:
            return ""
        try:
            rotation = await self._sector_rotation_agent.detect(tickers=tickers)  # type: ignore[attr-defined]
            if rotation is None:
                return ""

            insight = getattr(rotation, "actionable_insight", "") or ""
            crosscheck = getattr(rotation, "top_watchlist_crosscheck", []) or []

            if not insight and not crosscheck:
                return ""

            lines = ["\n\n## Sector Rotation Signal"]
            if insight:
                lines.append(insight)
            if crosscheck:
                items = crosscheck[:3]
                lines.append("Top crosscheck: " + ", ".join(
                    getattr(item, "ticker", str(item)) for item in items
                ))
            return "\n".join(lines)
        except Exception as exc:
            logger.warning(
                "briefing.sector_rotation_failed",
                tickers=tickers,
                error=str(exc),
            )
            return ""

    async def _build_investor_profile_context(self, user_id: str) -> str:
        """Build investor profile context using ContextBuilder.

        Returns a formatted string when session is available and ContextBuilder
        finds relevant data. Returns "" when session is None (scheduler, tests)
        or ContextBuilder finds no data — callers fall back to individual builders.
        Non-blocking.
        """
        if self._session is None:
            return ""
        try:
            builder = ContextBuilder(self._session)
            ctx = await builder.build(user_id=user_id)
            rendered = render_for_agent(ctx)
            return rendered
        except Exception as exc:
            logger.warning(
                "briefing.investor_profile_failed",
                user_id=user_id,
                error=str(exc),
            )
            return ""

    async def _build_portfolio_context(self, user_id: str) -> str:
        """Build portfolio P&L context string.

        Returns "" when pnl_service is not injected or any error occurs.
        Non-blocking.
        """
        if self._pnl_service is None:
            return ""
        try:
            pnl = await self._pnl_service.get_portfolio_pnl(user_id)  # type: ignore[attr-defined]
            if not pnl:
                return ""
            positions = getattr(pnl, "positions", []) or []
            if not positions:
                return ""
            lines = ["Portfolio P&L:"]
            for p in positions:
                ticker = getattr(p, "ticker", "?")
                pnl_pct = getattr(p, "unrealized_pct", None)
                weight = getattr(p, "weight_pct", None)
                parts = [f"  {ticker}"]
                if pnl_pct is not None:
                    parts.append(f"P&L={pnl_pct:+.1f}%")
                if weight is not None:
                    parts.append(f"weight={weight:.1f}%")
                lines.append(" ".join(parts))
            total_pct = getattr(pnl, "total_unrealized_pct", None)
            if total_pct is not None:
                lines.append(f"Total: {total_pct:+.2f}%")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning(
                "briefing.portfolio_context_failed",
                user_id=user_id,
                error=str(exc),
            )
            return ""

    async def _build_thesis_context(
        self,
        user_id: str,
        theses: list | None = None,
    ) -> str:
        """Build thesis context string for active theses.

        B4: accepts optional pre-fetched theses list to avoid calling
        list_for_user(active) twice per brief cycle.

        Returns "" when thesis_service is not injected or any error occurs.
        Non-blocking.
        """
        if self._thesis_service is None:
            return ""
        try:
            active_theses = theses
            if active_theses is None:
                active_theses = await self._thesis_service.list_for_user(  # type: ignore[attr-defined]
                    user_id=user_id, status="active"
                )
            if not active_theses:
                return ""
            lines = ["Active Theses:"]
            for t in active_theses:
                ticker = getattr(t, "ticker", "?")
                title = getattr(t, "title", "") or ""
                stop_loss = getattr(t, "stop_loss", None)
                parts = [f"  {ticker}: {title}"]
                if stop_loss is not None:
                    parts.append(f"[stop={stop_loss:,.0f}]")
                lines.append(" ".join(parts))
            return "\n".join(lines)
        except Exception as exc:
            logger.warning(
                "briefing.thesis_context_failed",
                user_id=user_id,
                error=str(exc),
            )
            return ""

    async def _build_lesson_context(self, user_id: str) -> str:
        """Build past decision lessons context string.

        Returns "" when session is not available or any error occurs.
        Non-blocking.
        """
        if self._session is None:
            return ""
        try:
            lesson_service = LessonService(self._session)
            lessons = await lesson_service.get_recent_lessons(user_id=user_id, limit=5)
            if not lessons:
                return ""
            lines = ["Past Decision Lessons:"]
            for lesson in lessons:
                ticker = getattr(lesson, "ticker", "?")
                summary = getattr(lesson, "lesson_summary", "") or ""
                lines.append(f"  {ticker}: {summary[:120]}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning(
                "briefing.lesson_context_failed",
                user_id=user_id,
                error=str(exc),
            )
            return ""

    async def _build_feedback_context(self, user_id: str) -> str:
        """Build brief feedback summary context string.

        Reads acted_rate from readmodel (lazy import) to avoid circular imports.
        Returns "" when:
          - session is None (scheduler, tests without DB)
          - fewer than 10 feedback samples exist (insufficient signal)
          - any error occurs
        Non-blocking. Wave 3 feature.
        """
        if self._session is None:
            return ""
        try:
            from src.readmodel.dashboard_service import DashboardService  # noqa: PLC0415

            dashboard = DashboardService(self._session)
            summary = await dashboard.get_feedback_summary(user_id=user_id)
            if summary is None:
                return ""
            acted_rate = getattr(summary, "acted_rate", None)
            sample_count = getattr(summary, "sample_count", 0) or 0
            if sample_count < 10 or acted_rate is None:
                return ""
            return (
                f"Feedback calibration: acted_rate={acted_rate:.0%} "
                f"(n={sample_count}). "
                "Adjust action specificity to match this investor's follow-through rate."
            )
        except Exception as exc:
            logger.debug(
                "briefing.feedback_context_failed",
                user_id=user_id,
                error=str(exc),
            )
            return ""
