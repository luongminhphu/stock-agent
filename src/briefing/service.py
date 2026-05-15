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
  Non-blocking: any failure returns "" and brief is unaffected.

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
  _fetch_conviction_history() is non-blocking: DB errors return None silently.
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
from src.briefing.models import BriefFeedback, BriefSnapshot
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
        watchlist_service:      reads user watchlist tickers.
        quote_service:          fetches bulk market quotes.
        briefing_agent:         AI agent that writes the brief narrative.
        pnl_service:            optional — reads open position P&L for portfolio context.
                                Pass None to skip portfolio section gracefully.
        thesis_service:         optional — reads active theses for thesis context injection.
                                When provided, stop_loss levels and key assumptions are
                                formatted and sent to the AI so it can force ACT_TODAY
                                for any ticker approaching invalidation.
                                Pass None to skip thesis section gracefully.
        session:                AsyncSession for persisting BriefSnapshot, reading past
                                decision lessons via LessonService, building investor
                                profile context via ContextBuilder, and reading feedback
                                summary via DashboardService (Wave 3).
                                Pass None to skip persistence, lesson injection,
                                investor profile injection, and feedback injection.
        sector_rotation_agent:  optional — AI agent that detects sector divergence signals.
                                When provided, its actionable_insight and top watchlist
                                crosscheck items are appended to market_context so the
                                BriefingAgent can factor in rotation dynamics.
                                Pass None (default) to skip — preserves existing behavior.
        thesis_judge_agent:     optional — AI agent that cross-checks active theses against
                                current market signals. When provided, verdicts (weakening /
                                invalidated) are formatted and appended to market_context
                                AFTER sector rotation block, BEFORE BriefingAgent LLM call.
                                Only non-ON_TRACK verdicts are emitted to avoid noise.
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

        outcome must be one of: "acted" | "watching" | "skipped".
        Append-only — does not overwrite previous feedback rows.
        Failures are logged and swallowed so a DB error never surfaces
        to the Discord interaction handler.
        """
        if self._session is None:
            logger.warning(
                "briefing.record_feedback.no_session",
                brief_snapshot_id=brief_snapshot_id,
                user_id=user_id,
                outcome=outcome,
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
            await self._session.commit()
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

        Returns a dict with keys:
          tickers, market_context, portfolio_context, thesis_context,
          past_lessons, investor_profile, feedback_summary,
          context_source, sector_rotation_injected, thesis_judge_ran.
        """
        t_total = time.monotonic()

        t0 = time.monotonic()
        tickers = await self._get_watchlist_tickers(user_id)
        watchlist_ms = round((time.monotonic() - t0) * 1000)

        t0 = time.monotonic()
        if self._thesis_judge_agent is not None:
            # Fetch quotes once — reused by both market_context and thesis_judge_block
            try:
                _raw_quotes = await self._quote_service.get_bulk_quotes(tickers)  # type: ignore[attr-defined]
                _quotes_by_ticker = {q.ticker: q for q in _raw_quotes}
            except Exception:
                _quotes_by_ticker = {}
            market_context = await self._build_market_context_with_judge(
                user_id=user_id, tickers=tickers, phase=phase, quotes=_quotes_by_ticker
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
            thesis_context = await self._build_thesis_context(user_id)
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
        # We track whether it was injected for observability in logs only.
        sector_rotation_injected = (
            self._sector_rotation_agent is not None and bool(tickers)
        )

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
            "thesis_judge_ran": self._thesis_judge_agent is not None,
        }

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

    async def _build_market_context(self, tickers: list[str], phase: str) -> str:
        now = datetime.now().strftime("%H:%M %d/%m/%Y")
        if not tickers:
            return (
                f"Thời điểm: {now}. Không có mã nào trong watchlist. "
                f"Hãy viết {phase} brief ở mức thị trường chung, nhấn mạnh quản trị rủi ro."
            )

        try:
            quotes = await self._quote_service.get_bulk_quotes(tickers)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("briefing.quote_fetch_failed", tickers=tickers, error=str(exc))
            return (
                f"Thời điểm: {now}. Không lấy được quote cho watchlist {', '.join(tickers)}. "
                f"Hãy viết {phase} brief thận trọng, nêu rõ thiếu dữ liệu giá realtime."
            )

        lines = [f"Thời điểm: {now}. Pha: {phase}.", "Watchlist snapshot:"]
        for q in quotes:
            try:
                info = symbol_registry.resolve(q.ticker)
                meta = f" | {info.name} | Ngành: {info.sector}"
            except Exception:
                meta = ""

            volume = getattr(q, "volume", None)
            volume_text = f", volume={volume:,}" if volume is not None else ""
            lines.append(
                f"- {q.ticker}{meta}: giá={q.price:,.0f}, change={q.change:,.0f}, "
                f"change_pct={q.change_pct:.2f}%{volume_text}"
            )
        lines.append(
            "Tập trung vào mã biến động mạnh, tín hiệu risk-on/risk-off, và watchlist-specific alerts."
        )

        # Sector rotation block — optional, non-blocking.
        rotation_block = await self._build_sector_rotation_block(tickers)
        if rotation_block:
            lines.append(rotation_block)

        return "\n".join(lines)

    async def _build_market_context_with_judge(
        self, user_id: str, tickers: list[str], phase: str, quotes: dict
    ) -> str:
        """Build market context with thesis judge verdicts appended.

        Thin wrapper over _build_market_context + _build_thesis_judge_block.
        Called by _collect_contexts when thesis_judge_agent is injected.
        Falls back to plain _build_market_context on any error.

        Quotes are pre-fetched by _collect_contexts and passed in to avoid
        a second network call. _build_market_context internally calls
        get_bulk_quotes again — this is acceptable duplication since
        _build_market_context is also used standalone (scheduler, tests).

        Args:
            user_id:  For loading active theses via thesis_service.
            tickers:  Watchlist tickers (already fetched).
            phase:    "morning" | "eod".
            quotes:   Pre-fetched quote objects keyed by ticker (may be empty dict).
        """
        base = await self._build_market_context(tickers, phase=phase)
        judge_block = await self._build_thesis_judge_block(user_id=user_id, quotes=quotes)
        if judge_block:
            return base + judge_block
        return base

    async def _fetch_last_review_summary(self, thesis_id: int | str) -> str | None:
        """Fetch and format the latest ThesisReview for a thesis as a compact string.

        Used by _build_thesis_judge_block to inject review continuity into
        signal_context["last_review_summary"] before each Judge run, so the
        Judge knows what verdict was given last time and can anchor its delta.

        Returns None when:
        - session is None
        - no ThesisReview row exists for this thesis
        - thesis_id cannot be cast to int
        - any DB error occurs (non-blocking)

        Owner: briefing (adapter). Reads thesis segment DB via ThesisRepository.
        """
        if self._session is None:
            return None
        try:
            # Lazy import keeps briefing decoupled from thesis at module level.
            from src.thesis.repository import ThesisRepository

            repo = ThesisRepository(self._session)
            thesis_id_int = int(thesis_id)
            review = await repo.get_latest_review(thesis_id_int)
            if review is None:
                return None

            # Format as a compact anchor for the Judge prompt.
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

            # risk_signals is stored as JSON string in the model
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

        Reads the last 4 reviews (rows 2-5, skipping the most recent which is
        already captured by _fetch_last_review_summary) and maps them to:
          [{"date": "dd/mm/yyyy", "verdict": str, "confidence": float | None}, ...]
        ordered newest-first.

        This gives ThesisJudgeAgent a longitudinal view of conviction drift
        rather than a single point-in-time snapshot, enabling it to detect:
        - accumulating WEAKENING across multiple runs
        - a reversal that contradicts a long BULLISH streak
        - a confidence trend (declining even if verdict unchanged)

        Returns None when:
        - session is None
        - thesis_id cannot be cast to int
        - fewer than 2 reviews exist (history not meaningful yet)
        - any DB error occurs (non-blocking)

        Owner: briefing (adapter). Reads thesis segment DB via ThesisRepository.
        """
        if self._session is None:
            return None
        try:
            from src.thesis.repository import ThesisRepository

            repo = ThesisRepository(self._session)
            thesis_id_int = int(thesis_id)
            # Fetch 5 reviews (newest first), skip index 0 (already in last_review_summary)
            reviews = await repo.list_reviews_by_thesis(thesis_id_int, limit=5)
            history_rows = reviews[1:]  # skip the most recent
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
        except Exception as exc:
            logger.debug(
                "briefing.fetch_conviction_history_failed",
                thesis_id=thesis_id,
                error=str(exc),
            )
            return None

    async def _build_thesis_judge_block(self, user_id: str, quotes: dict) -> str:
        """Run ThesisJudgeAgent against active theses and format non-ON_TRACK verdicts.

        Non-blocking: returns "" if agent not injected, thesis_service not injected,
        no active theses, or any error occurs.
        Only emits verdicts with verdict != ON_TRACK to avoid noise in brief context.

        P4 — last-review continuity:
          For each thesis, _fetch_last_review_summary() is called and the result
          is injected into signal_context["last_review_summary"]. This anchors the
          Judge verdict to the previous review so it can detect real deltas rather
          than generating contradictory verdicts in isolation.

        P4 wave 2 — conviction history:
          For each thesis, _fetch_conviction_history() is called and the result
          is passed as conviction_history in the trigger dict. ThesisJudgeAgent
          uses this to detect longitudinal conviction drift, not just point-in-time
          delta from the last review.

        Owner: briefing (adapter). Judge logic stays in ai segment.
        Cap: max 5 theses per run, max 2 challenged_assumptions and 2 new_risks per
             verdict, reasoning truncated at 120 chars — prevents context bloat.
        """
        if self._thesis_judge_agent is None or self._thesis_service is None:
            return ""
        try:
            theses = await self._thesis_service.list_for_user(  # type: ignore[attr-defined]
                user_id=user_id, status="active"
            )
            if not theses:
                return ""

            triggers = []
            for t in theses:
                assumptions = getattr(t, "assumptions", []) or []
                catalysts = getattr(t, "catalysts", []) or []
                invalidation = getattr(t, "invalidation_conditions", []) or []

                # Build minimal signal_context from pre-fetched quote data
                ticker_quote = quotes.get(t.ticker)
                signal_context: dict = {
                    "watchdog_verdict": None,
                    "urgency": "MEDIUM",
                    "trigger_reason": "scheduled_brief_check",
                    "risk_flags": [],
                }
                if ticker_quote is not None:
                    change_pct = getattr(ticker_quote, "change_pct", None)
                    if change_pct is not None and abs(change_pct) >= 3.0:
                        signal_context["urgency"] = "HIGH"
                        signal_context["risk_flags"] = ["price_spike"]
                        signal_context["trigger_reason"] = (
                            f"change_pct={change_pct:+.2f}%"
                        )

                # P4: inject last review summary for verdict continuity
                thesis_id = getattr(t, "id", t.ticker)
                last_review_summary = await self._fetch_last_review_summary(thesis_id)
                if last_review_summary:
                    signal_context["last_review_summary"] = last_review_summary

                # P4 wave 2: inject conviction history for longitudinal drift detection
                conviction_history = await self._fetch_conviction_history(thesis_id)

                triggers.append({
                    "thesis_id": str(thesis_id),
                    "ticker": t.ticker,
                    "thesis_title": getattr(t, "title", ""),
                    "thesis_summary": getattr(t, "summary", ""),
                    "assumptions": [
                        {
                            "id": getattr(a, "id", i),
                            "description": getattr(a, "description", str(a)),
                            "status": "active",
                        }
                        for i, a in enumerate(assumptions[:5])
                    ],
                    "catalysts": [
                        {
                            "id": getattr(c, "id", i),
                            "description": getattr(c, "description", str(c)),
                            "status": "pending",
                        }
                        for i, c in enumerate(catalysts[:3])
                    ],
                    "invalidation_conditions": [
                        getattr(ic, "description", str(ic))
                        if hasattr(ic, "description")
                        else str(ic)
                        for ic in invalidation[:3]
                    ],
                    "signal_context": signal_context,
                    "conviction_history": conviction_history,
                })

            if not triggers:
                return ""

            verdicts = await self._thesis_judge_agent.run_batch(triggers[:5])

            # Filter to non-ON_TRACK only — emit actionable verdicts only
            from src.ai.schemas import ThesisJudgeVerdict

            actionable = [
                v for v in verdicts
                if v.verdict != ThesisJudgeVerdict.ON_TRACK
            ]
            if not actionable:
                return ""

            lines = ["", "--- Thesis Judge Verdicts ---"]
            for v in actionable:
                challenged = "; ".join(
                    a.assumption_text
                    for a in (v.challenged_assumptions or [])[:2]
                )
                new_risks = "; ".join((v.new_risks or [])[:2])
                lines.append(
                    f"[{v.ticker}] verdict={v.verdict.value} action={v.action}"
                )
                if challenged:
                    lines.append(f"  challenged: {challenged}")
                if new_risks:
                    lines.append(f"  new_risks: {new_risks}")
                lines.append(f"  reasoning: {v.reasoning[:120]}")

            logger.info(
                "briefing.thesis_judge_block",
                user_id=user_id,
                total_theses=len(triggers),
                actionable_count=len(actionable),
            )
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.thesis_judge_block_failed", error=str(exc))
            return ""

    async def _build_sector_rotation_block(self, tickers: list[str]) -> str:
        """Build sector rotation divergence block for market context injection.

        Calls SectorRotationAgent.analyze(tickers) and formats actionable_insight
        plus the top 3 watchlist_crosscheck items into a plain-text block.

        Non-blocking: returns "" if agent is not injected, output is empty,
        or any error occurs. Brief generation must never be blocked by this.

        Owner: briefing (adapter). Rotation logic stays in ai segment.
        Cap: watchlist_crosscheck[:3] to avoid context bloat.
        """
        if self._sector_rotation_agent is None or not tickers:
            return ""
        try:
            result = await self._sector_rotation_agent.analyze(tickers=tickers)  # type: ignore[attr-defined]
            if not result:
                return ""

            actionable = getattr(result, "actionable_insight", None)
            crosscheck = getattr(result, "watchlist_crosscheck", None) or []

            if not actionable and not crosscheck:
                return ""

            lines = ["", "--- Sector Rotation Signal ---"]
            if actionable:
                lines.append(f"Tín hiệu rotation: {actionable}")
            if crosscheck:
                lines.append("Divergence watchlist:")
                for item in crosscheck[:3]:
                    lines.append(f"  • {item}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.sector_rotation_block_failed", error=str(exc))
            return ""

    async def _build_portfolio_context(self, user_id: str) -> str:
        """Build portfolio P&L snapshot string for AI context injection.

        Fallback path — only called when ContextBuilder did not produce an
        investor_profile block (session=None or no data found).
        """
        if self._pnl_service is None:
            return ""
        try:
            pnl = await self._pnl_service.get_portfolio_pnl(user_id)  # type: ignore[attr-defined]
            if not pnl.positions:
                return ""

            lines = [
                f"Portfolio: {len(pnl.positions)} vị thế đang mở, "
                f"tổng giá trị thị trường={pnl.total_market_value:,.0f} VNĐ, "
                f"lãi/lỗ chưa thực hiện={pnl.total_unrealized_pnl:+,.0f} VNĐ "
                f"({pnl.total_unrealized_pct:+.2f}%).",
                "Chi tiết từng vị thế:",
            ]
            for p in pnl.positions:
                lines.append(
                    f"- {p.ticker}: giá_vốn={p.avg_cost:,.0f}, "
                    f"giá_tt={p.current_price:,.0f}, "
                    f"lãi/lỗ={p.unrealized_pnl:+,.0f} ({p.unrealized_pct:+.2f}%)"
                )
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.portfolio_context_failed", user_id=user_id, error=str(exc))
            return ""

    async def _build_thesis_context(self, user_id: str) -> str:
        """Build active thesis summary string for AI context injection.

        Fallback path — only called when ContextBuilder did not produce an
        investor_profile block (session=None or no data found).
        """
        if self._thesis_service is None:
            return ""
        try:
            theses = await self._thesis_service.list_for_user(  # type: ignore[attr-defined]
                user_id=user_id, status="active"
            )
            if not theses:
                return ""

            lines = [f"Có {len(theses)} thesis đang active:"]
            for t in theses:
                stop_loss_str = (
                    f", stop_loss={t.stop_loss:,.0f}"
                    if getattr(t, "stop_loss", None) is not None
                    else " (chưa đặt stop_loss)"
                )
                target_str = (
                    f", target={t.target_price:,.0f}"
                    if getattr(t, "target_price", None) is not None
                    else ""
                )
                lines.append(
                    f"- [{t.ticker}] {t.title}{stop_loss_str}{target_str}"
                )
                assumptions = getattr(t, "assumptions", []) or []
                for a in assumptions[:3]:
                    desc = getattr(a, "description", str(a))
                    lines.append(f"  • Giả định: {desc}")
            lines.append(
                "Nếu giá hiện tại (từ Watchlist snapshot) đang tiếp cận stop_loss của bất kỳ thesis —"
                " xuất ACT_TODAY cho ticker đó."
            )
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.thesis_context_failed", user_id=user_id, error=str(exc))
            return ""

    async def _build_lesson_context(self, user_id: str) -> str:
        """Build past decision lessons string for AI context injection.

        Fallback path — only called when ContextBuilder did not produce an
        investor_profile block (session=None or no data found).
        """
        if self._session is None:
            return ""
        try:
            lesson_service = LessonService(self._session)
            lessons = await lesson_service.get_recent_lessons(user_id=user_id, limit=5)
            if not lessons:
                return ""

            lines = ["Bài học từ quyết định gần đây:"]
            for lesson in lessons:
                lines.append(f"- {lesson}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("briefing.lesson_context_failed", user_id=user_id, error=str(exc))
            return ""

    async def _build_investor_profile_context(self, user_id: str) -> str:
        """Build comprehensive investor profile block via ContextBuilder.

        Returns "" when session is None (scheduler, tests without DB).
        ContextBuilder aggregates thesis health, portfolio bias, and recent
        lessons — callers must zero out the three individual context strings
        when this returns a non-empty value (dedup rule).
        """
        if self._session is None:
            return ""
        try:
            t0 = time.monotonic()
            ctx = await ContextBuilder(self._session).build(user_id=user_id)
            context_builder_build_ms = round((time.monotonic() - t0) * 1000)
            logger.debug(
                "briefing.context_builder_build_complete",
                user_id=user_id,
                has_profile=bool(ctx.risk_appetite),
                has_thesis=bool(ctx.active_thesis_summary),
                has_lessons=bool(ctx.recent_lessons),
                has_portfolio=bool(ctx.portfolio_bias),
                build_ms=context_builder_build_ms,
            )
            rendered = render_for_agent(ctx)
            return rendered
        except Exception as exc:
            logger.warning(
                "briefing.investor_profile_context_failed",
                user_id=user_id,
                error=str(exc),
            )
            return ""

    async def _build_feedback_context(self, user_id: str) -> str:
        """Build feedback calibration block from readmodel DashboardService.

        Wave 3 feature: reads acted_rate from readmodel to calibrate AI
        action count/specificity. Returns "" when:
        - session is None
        - fewer than 10 feedback samples exist
        - any error occurs (DB, import, timeout)

        This is intentionally imported lazily to keep the briefing segment
        decoupled from readmodel at import time.
        """
        if self._session is None:
            return ""
        try:
            from src.readmodel.dashboard_service import DashboardService  # lazy import

            dashboard = DashboardService(self._session)
            summary = await dashboard.get_brief_feedback_summary(user_id=user_id)
            if summary is None or summary.get("total_count", 0) < 10:
                return ""

            acted_rate = summary.get("acted_rate", 0.0)
            total = summary.get("total_count", 0)

            if acted_rate >= 0.7:
                calibration = (
                    "Nhà đầu tư này có tỷ lệ hành động cao (acted_rate="
                    f"{acted_rate:.0%}, n={total}). Ưu tiên recommendations cụ thể, "
                    "actionable. Giữ danh sách ACT_TODAY ngắn và có độ tin cậy cao."
                )
            elif acted_rate <= 0.3:
                calibration = (
                    "Nhà đầu tư này ít hành động theo brief (acted_rate="
                    f"{acted_rate:.0%}, n={total}). Tăng tính thuyết phục: "
                    "giải thích rõ lý do, nêu rủi ro nếu không hành động."
                )
            else:
                calibration = (
                    f"Tỷ lệ hành động trung bình (acted_rate={acted_rate:.0%}, n={total}). "
                    "Cân bằng giữa recommendations và context."
                )

            return calibration
        except Exception as exc:
            logger.warning("briefing.feedback_context_failed", user_id=user_id, error=str(exc))
            return ""
