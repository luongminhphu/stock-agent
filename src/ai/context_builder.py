"""ContextBuilder — assemble investor context before each AI call.

Owner: ai segment.
Callers: briefing.BriefingService, thesis.PreTradeService.

Responsibility:
    Aggregate context from multiple domain segments into a single
    InvestorContext object that agents can render into their prompts.

Design rules:
    - Read-only access to domain data; never writes.
    - Each source is fetched independently; a failure in one source
      MUST NOT block the others (graceful degradation).
    - Does NOT import from: bot, briefing, watchlist, api, readmodel.
    - Dependency direction: ai → platform, thesis (read), portfolio (read).
    - Session is injected by caller; ContextBuilder does not own sessions.

Usage::

    from src.ai.context_builder import ContextBuilder, render_for_agent

    async with AsyncSessionLocal() as session:
        ctx = await ContextBuilder(session).build()
    block = render_for_agent(ctx)
    # pass block as investor_profile= kwarg to prompt builders
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data contract
# ---------------------------------------------------------------------------


@dataclass
class InvestorContext:
    """Structured investor context passed to AI agents.

    All string fields default to empty string so agents can safely
    check ``if ctx.investor_profile_block`` without None guards.

    source_flags records which upstream sources were available at build
    time (name → bool). Agents may use this to adjust confidence when
    data is missing.
    """

    investor_profile_block: str = ""
    active_thesis_summary: str = ""
    recent_lessons: str = ""
    portfolio_bias: str = ""
    watchlist_signals: str = ""  # reserved — Wave 3
    built_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_flags: dict[str, bool] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """True when no meaningful context was available from any source."""
        return not any(
            [
                self.investor_profile_block,
                self.active_thesis_summary,
                self.recent_lessons,
                self.portfolio_bias,
            ]
        )


# ---------------------------------------------------------------------------
# Public render helper
# ---------------------------------------------------------------------------


def render_for_agent(ctx: InvestorContext) -> str:
    """Render InvestorContext as a compact plain-text block for prompt injection.

    Returns empty string when no context is available so callers can safely
    do ``if block: prompt += block`` without special-casing.

    Output example::

        === INVESTOR PROFILE ===
        Risk: medium — max drawdown 10%
        Style: fundamental | Horizon: swing to positional
        Focus: banking, consumer staples
        Win rate (30d): 62%  |  Avg hold: 18d
        Portfolio: Banking 42%, Real Estate 18%
        Patterns: FOMO buy at resistance; Sell too early on volatility
        Last lesson: Không chase breakout khi volume thấp hơn TB20
        === ACTIVE THESES (2) ===
        VCB: entry 88500 | stop 82000 | target 102000 | ACTIVE
        MWG: entry 45000 | stop 41000 | target 58000 | ACTIVE
        === RECENT LESSONS ===
        1. Không chase breakout khi volume thấp
        2. Thoát lệnh đúng SL, không giữ khi thesis bị vi phạm
        ========================
    """
    if ctx.is_empty():
        return ""

    sections: list[str] = []

    if ctx.investor_profile_block:
        sections.append(ctx.investor_profile_block)

    if ctx.active_thesis_summary:
        sections.append(ctx.active_thesis_summary)

    if ctx.recent_lessons:
        sections.append(ctx.recent_lessons)

    if ctx.portfolio_bias:
        sections.append(f"Portfolio bias: {ctx.portfolio_bias}")

    sections.append("========================")
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class ContextBuilder:
    """Assembles InvestorContext for a single AI call.

    Each ``_fetch_*`` method is fully independent and silently degrades
    to empty string on any exception. This ensures ContextBuilder.build()
    never raises and never blocks an AI call due to a missing data source.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(self) -> InvestorContext:
        """Build and return InvestorContext. Never raises."""
        ctx = InvestorContext()

        ctx.investor_profile_block, ctx.source_flags["investor_profile"] = (
            await self._fetch_investor_profile()
        )
        ctx.active_thesis_summary, ctx.source_flags["active_theses"] = (
            await self._fetch_active_theses()
        )
        ctx.recent_lessons, ctx.source_flags["recent_lessons"] = (
            await self._fetch_recent_lessons()
        )
        ctx.portfolio_bias, ctx.source_flags["portfolio_bias"] = (
            await self._fetch_portfolio_bias()
        )

        logger.info(
            "context_builder.built",
            sources_available=sum(ctx.source_flags.values()),
            sources_total=len(ctx.source_flags),
            is_empty=ctx.is_empty(),
        )
        return ctx

    # ------------------------------------------------------------------
    # Private fetchers — each returns (content: str, available: bool)
    # ------------------------------------------------------------------

    async def _fetch_investor_profile(self) -> tuple[str, bool]:
        """Read latest InvestorProfile snapshot → summary_for_ai.

        Falls back to StaticProfile.from_settings() + InvestorContext.to_prompt_block()
        when no DB snapshot exists yet (first run before scheduler has fired).
        """
        try:
            from src.platform.investor_profile import (
                InvestorContext as _InvCtx,
                InvestorProfileService,
                StaticProfile,
            )

            svc = InvestorProfileService(session=self._session)
            snapshot = await svc.get_latest()

            if snapshot and snapshot.summary_for_ai:
                # DB snapshot available — use pre-rendered block
                static = StaticProfile.from_settings()
                inv_ctx = _InvCtx(static=static, snapshot=snapshot)
                return inv_ctx.to_prompt_block(), True

            # Fallback: static profile only (first boot)
            static = StaticProfile.from_settings()
            inv_ctx = _InvCtx(static=static, snapshot=None)
            block = inv_ctx.to_prompt_block()
            if block:
                logger.info(
                    "context_builder.investor_profile_static_fallback",
                    reason="no_db_snapshot_yet",
                )
                return block, True
            return "", False
        except Exception as exc:
            logger.warning("context_builder.investor_profile_error", error=str(exc))
            return "", False

    async def _fetch_active_theses(self) -> tuple[str, bool]:
        """Summarise active theses: ticker | entry | stop | target | status."""
        try:
            from sqlalchemy import select

            from src.thesis.models import Thesis, ThesisStatus

            result = await self._session.execute(
                select(Thesis)
                .where(Thesis.status == ThesisStatus.ACTIVE)
                .order_by(Thesis.created_at.desc())
                .limit(10)
            )
            theses = result.scalars().all()
            if not theses:
                return "", False

            lines = [f"=== ACTIVE THESES ({len(theses)}) ==="]
            for t in theses:
                entry = f"{t.entry_price:,.0f}" if t.entry_price else "?"
                stop = f"{t.stop_loss:,.0f}" if t.stop_loss else "?"
                target = f"{t.target_price:,.0f}" if t.target_price else "?"
                lines.append(
                    f"{t.ticker}: entry {entry} | stop {stop} | target {target} | {t.status.value}"
                )
            return "\n".join(lines), True
        except Exception as exc:
            logger.warning("context_builder.active_theses_error", error=str(exc))
            return "", False

    async def _fetch_recent_lessons(self) -> tuple[str, bool]:
        """Return top 5 recent key_lesson strings from evaluated DecisionLogs."""
        try:
            from sqlalchemy import select

            from src.thesis.models import DecisionLog

            result = await self._session.execute(
                select(DecisionLog)
                .where(
                    DecisionLog.outcome_verdict.isnot(None),
                    DecisionLog.key_lesson.isnot(None),
                    DecisionLog.key_lesson != "",
                )
                .order_by(DecisionLog.outcome_evaluated_at.desc())
                .limit(5)
            )
            logs = result.scalars().all()
            if not logs:
                return "", False

            lines = ["=== RECENT LESSONS ==="]
            for i, log in enumerate(logs, 1):
                lines.append(f"{i}. {log.key_lesson}")
            return "\n".join(lines), True
        except Exception as exc:
            logger.warning("context_builder.recent_lessons_error", error=str(exc))
            return "", False

    async def _fetch_portfolio_bias(self) -> tuple[str, bool]:
        """Return short sector-weight string from open positions."""
        try:
            from sqlalchemy import select

            from src.portfolio.models import Position

            result = await self._session.execute(
                select(Position).where(Position.is_open.is_(True))
            )
            positions = result.scalars().all()
            if not positions:
                return "", False

            sector_totals: dict[str, float] = {}
            total_value = 0.0
            for pos in positions:
                sector = getattr(pos, "sector", None) or "Unknown"
                value = float(getattr(pos, "market_value", 0) or 0)
                sector_totals[sector] = sector_totals.get(sector, 0.0) + value
                total_value += value

            if total_value == 0:
                return "", False

            top = sorted(sector_totals.items(), key=lambda x: x[1], reverse=True)[:3]
            parts = [f"{s} {(v / total_value) * 100:.0f}%" for s, v in top if v > 0]
            return ", ".join(parts), True
        except Exception as exc:
            logger.warning("context_builder.portfolio_bias_error", error=str(exc))
            return "", False
