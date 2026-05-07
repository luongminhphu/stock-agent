"""Briefing service — owner of brief generation flow.

Owner: briefing segment.

Responsibilities:
- collect watchlist tickers from watchlist segment
- collect market context from market segment (quotes)
- collect portfolio P&L snapshot from portfolio segment (optional)
- collect active thesis context from thesis segment (optional)
- collect past decision lessons from thesis segment (optional, via LessonService)
- collect investor profile context from ai segment (optional, via ContextBuilder)
- call BriefingAgent for morning/EOD narrative
- persist BriefSnapshot via BriefSnapshotRepository
- return structured BriefOutput to adapters

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
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.briefing import BriefingAgent
from src.ai.context_builder import ContextBuilder, render_for_agent
from src.ai.schemas import BriefOutput
from src.briefing.models import BriefSnapshot
from src.briefing.repository import BriefSnapshotRepository
from src.market.registry import registry as symbol_registry
from src.platform.logging import get_logger
from src.thesis.lesson_service import LessonService
from src.watchlist.service import WatchlistService

logger = get_logger(__name__)


class BriefingService:
    """Orchestrates morning and end-of-day brief generation.

    Args:
        watchlist_service:  reads user watchlist tickers.
        quote_service:      fetches bulk market quotes.
        briefing_agent:     AI agent that writes the brief narrative.
        pnl_service:        optional — reads open position P&L for portfolio context.
                            Pass None to skip portfolio section gracefully.
        thesis_service:     optional — reads active theses for thesis context injection.
                            When provided, stop_loss levels and key assumptions are
                            formatted and sent to the AI so it can force ACT_TODAY
                            for any ticker approaching invalidation.
                            Pass None to skip thesis section gracefully.
        session:            AsyncSession for persisting BriefSnapshot, reading past
                            decision lessons via LessonService, and building investor
                            profile context via ContextBuilder.
                            Pass None to skip persistence, lesson injection, and
                            investor profile injection.
    """

    def __init__(
        self,
        watchlist_service: WatchlistService,
        quote_service: object,
        briefing_agent: BriefingAgent,
        pnl_service: object | None = None,
        thesis_service: object | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self._watchlist_service = watchlist_service
        self._quote_service = quote_service
        self._agent = briefing_agent
        self._pnl_service = pnl_service
        self._thesis_service = thesis_service
        self._session = session
        self._repo = BriefSnapshotRepository(session) if session is not None else None

    async def generate_morning_brief(self, user_id: str) -> BriefOutput:
        ctx = await self._collect_contexts(user_id, phase="morning")
        logger.info(
            "briefing.generate_morning",
            user_id=user_id,
            tickers=ctx["tickers"],
            has_portfolio=bool(ctx["portfolio_context"]),
            has_thesis=bool(ctx["thesis_context"]),
            has_lessons=bool(ctx["past_lessons"]),
            has_investor_profile=bool(ctx["investor_profile"]),
            context_source=ctx["context_source"],
        )
        result = await self._agent.morning_brief(
            market_context=ctx["market_context"],
            watchlist_tickers=ctx["tickers"],
            portfolio_context=ctx["portfolio_context"],
            thesis_context=ctx["thesis_context"],
            past_lessons=ctx["past_lessons"],
            investor_profile=ctx["investor_profile"],
        )
        await self._persist(user_id=user_id, phase="morning", output=result, tickers=ctx["tickers"])
        return result

    async def generate_eod_brief(self, user_id: str) -> BriefOutput:
        ctx = await self._collect_contexts(user_id, phase="eod")
        logger.info(
            "briefing.generate_eod",
            user_id=user_id,
            tickers=ctx["tickers"],
            has_portfolio=bool(ctx["portfolio_context"]),
            has_thesis=bool(ctx["thesis_context"]),
            has_lessons=bool(ctx["past_lessons"]),
            has_investor_profile=bool(ctx["investor_profile"]),
            context_source=ctx["context_source"],
        )
        result = await self._agent.eod_brief(
            market_context=ctx["market_context"],
            watchlist_tickers=ctx["tickers"],
            portfolio_context=ctx["portfolio_context"],
            thesis_context=ctx["thesis_context"],
            past_lessons=ctx["past_lessons"],
            investor_profile=ctx["investor_profile"],
        )
        await self._persist(user_id=user_id, phase="eod", output=result, tickers=ctx["tickers"])
        return result

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

        Returns a dict with keys:
          tickers, market_context, portfolio_context, thesis_context,
          past_lessons, investor_profile, context_source.
        """
        tickers = await self._get_watchlist_tickers(user_id)
        market_context = await self._build_market_context(tickers, phase=phase)

        # Try ContextBuilder first — it aggregates thesis + portfolio + lessons
        investor_profile = await self._build_investor_profile_context(user_id)

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
            portfolio_context = await self._build_portfolio_context(user_id)
            thesis_context = await self._build_thesis_context(user_id)
            past_lessons = await self._build_lesson_context(user_id)

        return {
            "tickers": tickers,
            "market_context": market_context,
            "portfolio_context": portfolio_context,
            "thesis_context": thesis_context,
            "past_lessons": past_lessons,
            "investor_profile": investor_profile,
            "context_source": context_source,
        }

    async def _persist(
        self,
        user_id: str,
        phase: str,
        output: BriefOutput,
        tickers: list[str],
    ) -> None:
        """Save a BriefSnapshot if a session was injected. Failures are
        logged and swallowed so a DB error never blocks the brief delivery.
        """
        if self._repo is None:
            return
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
        except Exception as exc:
            logger.error(
                "briefing.snapshot_save_failed",
                user_id=user_id,
                phase=phase,
                error=str(exc),
            )

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
        return "\n".join(lines)

    async def _build_portfolio_context(self, user_id: str) -> str:
        """Build portfolio P&L snapshot string for AI context injection.

        Fallback path — only called when ContextBuilder did not produce an
        investor_profile block (session=None or no data found).

        Returns empty string if pnl_service is not injected, portfolio is
        empty, or any error occurs — brief generation must never be blocked
        by portfolio data unavailability.
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

        Formats each active thesis as: ticker, title, stop_loss (if set),
        and up to 3 key assumptions. This gives the AI enough data to
        detect when a price is approaching invalidation territory.

        Returns empty string if thesis_service is not injected, no active
        theses exist, or any error occurs — brief generation must never be
        blocked by thesis data unavailability.
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

        Queries the last 5 evaluated DecisionLog records for this user via
        LessonService and returns a formatted string for prompt injection.

        Returns empty string if session is not injected, no evaluated
        decisions exist yet, or any error occurs — brief generation must
        never be blocked by lesson data unavailability.
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

        Calls ContextBuilder(session).build(user_id) → render_for_agent() to
        produce a pre-rendered plain-text block that BriefingAgent injects into
        the morning/EOD prompt for personalised prioritized_actions.

        This block already contains:
          - [Investor profile] — risk appetite, avoid list, preferred sectors
          - [Thesis health]    — ThesisHealthSnapshot V3 (urgency-sorted)
          - [Portfolio hiện tại] — P&L with sector key_metrics
          - [Recent lessons]   — last 3 decision lessons

        When this method returns a non-empty string, _collect_contexts() will
        skip the individual portfolio/thesis/lesson builders to avoid sending
        the same facts twice to the AI.

        Owner: ai segment (ContextBuilder). This method is a thin adapter —
        it does NOT contain profile assembly logic.

        Returns empty string when:
        - session is not injected (scheduler/test without DB)
        - ContextBuilder finds no data in any source
        - any unexpected error occurs
        Brief generation must never be blocked by profile unavailability.
        """
        if self._session is None:
            return ""
        try:
            ctx = await ContextBuilder(self._session).build(user_id=user_id)
            return render_for_agent(ctx)
        except Exception as exc:
            logger.warning("briefing.investor_profile_context_failed", error=str(exc))
            return ""
