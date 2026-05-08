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
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.briefing import BriefingAgent
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
    ) -> None:
        self._watchlist_service = watchlist_service
        self._quote_service = quote_service
        self._agent = briefing_agent
        self._pnl_service = pnl_service
        self._thesis_service = thesis_service
        self._session = session
        self._repo = BriefSnapshotRepository(session) if session is not None else None
        self._sector_rotation_agent = sector_rotation_agent

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

        Returns a dict with keys:
          tickers, market_context, portfolio_context, thesis_context,
          past_lessons, investor_profile, feedback_summary,
          context_source, sector_rotation_injected.
        """
        t_total = time.monotonic()

        t0 = time.monotonic()
        tickers = await self._get_watchlist_tickers(user_id)
        watchlist_ms = round((time.monotonic() - t0) * 1000)

        t0 = time.monotonic()
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
            for pos in pnl.positions:
                pct_str = f"{pos.unrealized_pct:+.2f}%"
                pnl_str = f"{pos.unrealized_pnl:+,.0f} VNĐ"
                lines.append(
                    f"- {pos.ticker}: giá vốn={pos.avg_cost:,.0f}, "
                    f"giá hiện tại={pos.current_price:,.0f}, "
                    f"lãi/lỗ={pnl_str} ({pct_str}), "
                    f"khối lượng={pos.qty:,.0f}"
                )
            if pnl.errors:
                lines.append(
                    f"Lưu ý: không lấy được giá cho {', '.join(pnl.errors.keys())} — bỏ qua các mã này."
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
        """Build past decision lesson string for AI personalisation.

        Fallback path — only called when ContextBuilder did not produce an
        investor_profile block (session=None or no data found).
        """
        if self._session is None:
            return ""
        try:
            svc = LessonService(self._session)
            return await svc.build_lesson_context(user_id=user_id)
        except Exception as exc:
            logger.warning("briefing.lesson_context_failed", user_id=user_id, error=str(exc))
            return ""

    async def _build_investor_profile_context(self, user_id: str) -> str:
        """Build investor profile block via ContextBuilder.

        When this method returns a non-empty string, _collect_contexts() will
        skip the individual portfolio/thesis/lesson builders to avoid sending
        the same facts twice to the AI.
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
                has_memory=bool(ctx.memory_context_block),
                build_ms=context_builder_build_ms,
            )
            return render_for_agent(ctx)
        except Exception as exc:
            logger.warning("briefing.investor_profile_context_failed", error=str(exc))
            return ""

    async def _build_feedback_context(self, user_id: str) -> str:
        """Build feedback calibration string for AI tone adjustment.

        Cross-segment read: calls readmodel.DashboardService (lazy import to
        avoid circular dependency at module load time).

        Requires minimum 10 feedback samples in the last 30 days before
        injecting — below that threshold the string is empty and brief
        generation is unaffected.

        The three tone buckets map acted_rate to concrete output rules:
          low  (<25%): max 2 ACT_TODAY, 1-sentence actions
          mid  (25-65%): keep count, require confidence >= 0.7 for ACT_TODAY
          high (>65%): keep count and detail, focus on clear reason

        Feedback NEVER overrides risk_appetite from investor_profile —
        that constraint is documented in SYSTEM_PROMPT.

        Never raises — all exceptions are logged and swallowed.
        """
        if self._session is None:
            return ""
        try:
            from src.readmodel.dashboard_service import DashboardService  # lazy import

            summary = await DashboardService(self._session).get_brief_feedback_summary(user_id)

            acted_rate = summary.get("acted_rate_30d")
            total = summary.get("total_feedbacks_30d", 0)

            if acted_rate is None or total < 10:
                return ""

            if acted_rate < 0.25:
                tone = (
                    "acted_rate thấp (<25%) — user thường bỏ qua brief. "
                    "Viết action cực kỳ ngắn, cụ thể, có thể thực hiện ngay trong 1 bước. "
                    "Giảm số lượng ACT_TODAY xuống còn tối đa 2."
                )
                tone_bucket = "low"
            elif acted_rate > 0.65:
                tone = (
                    "acted_rate cao (>65%) — user thường follow brief. "
                    "Giữ nguyên độ chi tiết hiện tại. "
                    "Đảm bảo reason đủ rõ để user tự tin thực hiện."
                )
                tone_bucket = "high"
            else:
                tone = (
                    "acted_rate trung bình — user hành động có chọn lọc. "
                    "Ưu tiên ACT_TODAY có confidence >= 0.7. "
                    "WATCH_MORE và SKIP_TODAY nên có reason phân biệt rõ ràng."
                )
                tone_bucket = "mid"

            logger.info(
                "briefing.feedback_context_built",
                user_id=user_id,
                acted_rate=acted_rate,
                total_feedbacks=total,
                tone_bucket=tone_bucket,
            )

            return (
                f"Feedback 30 ngày qua: acted_rate={acted_rate:.0%} "
                f"(trên {total} briefs). {tone}"
            )
        except Exception as exc:
            logger.warning("briefing.feedback_context_failed", user_id=user_id, error=str(exc))
            return ""
