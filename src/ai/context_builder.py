"""ContextBuilder — assembles investor context for AI agent calls.

Owner: ai segment.
Callers: ai/agents/*.py only.

Builds an InvestorContext from:
  - platform.investor_profile (static profile + risk settings)
  - thesis segment (active theses summary)
  - thesis.lesson_service (recent decision lessons)
  - portfolio segment (portfolio bias — sector exposure, P&L tilt)
  - ai.memory (MemoryContext — episodic + semantic memory)  ← NEW in V2

Boundary rule:
  ContextBuilder knows ABOUT domain segments but does NOT own their logic.
  It calls their public read APIs; never writes to them.

Backward compatibility:
  ContextBuilder(session) still works exactly as before.
  memory injection is additive — if MemoryService fails, context is still built.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


@dataclass
class InvestorContext:
    """Assembled investor context — passed to render_for_agent()."""

    # From platform.investor_profile
    risk_appetite: str = ""
    avoid_list: list[str] = field(default_factory=list)
    preferred_sectors: list[str] = field(default_factory=list)
    trading_style: str = ""
    notes: str = ""

    # From thesis segment
    active_thesis_summary: str = ""

    # From thesis.lesson_service
    recent_lessons: str = ""

    # From portfolio segment
    portfolio_bias: str = ""

    # From ai.memory (NEW in V2)
    memory_context_block: str = ""


class ContextBuilder:
    """Assembles InvestorContext by querying domain segment read-APIs.

    Usage::

        ctx = await ContextBuilder(session).build(user_id="user_001")
        profile_str = render_for_agent(ctx)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(self, user_id: str | None = None) -> InvestorContext:
        """Fetch all context layers concurrently and assemble InvestorContext.

        All sub-fetches are fire-and-forget — if any fails, the rest still
        succeed. ContextBuilder never blocks AI calls due to data errors.
        """
        results = await asyncio.gather(
            self._fetch_investor_profile(user_id),
            self._fetch_thesis_summary(user_id),
            self._fetch_recent_lessons(user_id),
            self._fetch_portfolio_bias(user_id),
            self._fetch_memory_context(user_id),   # NEW
            return_exceptions=True,
        )

        ctx = InvestorContext()
        _apply_profile(ctx, results[0])
        _apply_thesis(ctx, results[1])
        _apply_lessons(ctx, results[2])
        _apply_portfolio(ctx, results[3])
        _apply_memory(ctx, results[4])              # NEW

        return ctx

    # ------------------------------------------------------------------
    # Private fetch methods — each returns a plain value or raises
    # ------------------------------------------------------------------

    async def _fetch_investor_profile(self, user_id: str | None) -> dict:
        try:
            from src.platform.investor_profile import InvestorProfileService

            svc = InvestorProfileService(self._session)
            profile = await svc.get_profile(user_id=user_id)
            if profile is None:
                return {}
            return {
                "risk_appetite": getattr(profile, "risk_appetite", "") or "",
                "avoid_list": getattr(profile, "avoid_list", []) or [],
                "preferred_sectors": getattr(profile, "preferred_sectors", []) or [],
                "trading_style": getattr(profile, "trading_style", "") or "",
                "notes": getattr(profile, "notes", "") or "",
            }
        except Exception as exc:
            logger.warning("context_builder.investor_profile_failed", error=str(exc))
            return {}

    async def _fetch_thesis_summary(self, user_id: str | None) -> str:
        try:
            from src.thesis.service import ThesisService

            svc = ThesisService(self._session)
            theses = await svc.list_active(user_id=user_id)
            if not theses:
                return ""
            lines = []
            for t in theses[:5]:  # cap at 5 to avoid prompt bloat
                ticker = getattr(t, "ticker", "?")
                direction = getattr(t, "direction", "")
                summary = getattr(t, "summary", "") or ""
                lines.append(f"- {ticker} ({direction}): {summary[:120]}")
            return "Active theses:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("context_builder.thesis_summary_failed", error=str(exc))
            return ""

    async def _fetch_recent_lessons(self, user_id: str | None) -> str:
        try:
            from src.thesis.lesson_service import LessonService

            svc = LessonService(self._session)
            lessons = await svc.get_recent(user_id=user_id, limit=5)
            if not lessons:
                return ""
            lines = []
            for lesson in lessons:
                date_str = getattr(lesson, "created_at", "")  
                text = getattr(lesson, "key_lesson", "") or ""
                lines.append(f"- [{date_str}] {text[:150]}")
            return "Recent decision lessons:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("context_builder.recent_lessons_failed", error=str(exc))
            return ""

    async def _fetch_portfolio_bias(self, user_id: str | None) -> str:
        """Fetch portfolio bias block from portfolio segment public contract.

        Calls portfolio.get_portfolio_context(session, user_id) which
        returns a PortfolioContext. Degrades gracefully to empty string.
        """
        try:
            from src.portfolio import get_portfolio_context

            port_ctx = await get_portfolio_context(self._session, user_id)
            if port_ctx is None:
                return ""
            return str(port_ctx)
        except Exception as exc:
            logger.warning("context_builder.portfolio_bias_failed", error=str(exc))
            return ""

    async def _fetch_memory_context(self, user_id: str | None) -> str:
        """Fetch L2 + L3 memory context for the investor.

        Returns rendered text block, or empty string if unavailable.
        Owner: ai.memory (MemoryService) — not this method.
        """
        if not user_id:
            return ""
        try:
            from src.ai.memory.memory_service import MemoryService

            mem_ctx = await MemoryService.get_memory_context(
                session=self._session,
                user_id=user_id,
            )
            if mem_ctx.is_empty():
                return ""
            rendered = mem_ctx.render()
            logger.debug(
                "context_builder.memory_context_loaded",
                user_id=user_id,
                episode_count=len(mem_ctx.recent_episodes),
                has_snapshot=mem_ctx.latest_snapshot is not None,
            )
            return rendered
        except Exception as exc:
            logger.warning(
                "context_builder.memory_context_failed",
                user_id=user_id,
                error=str(exc),
            )
            return ""


# ---------------------------------------------------------------------------
# Applicator helpers (keep build() readable)
# ---------------------------------------------------------------------------


def _apply_profile(ctx: InvestorContext, result) -> None:
    if isinstance(result, dict):
        ctx.risk_appetite = result.get("risk_appetite", "")
        ctx.avoid_list = result.get("avoid_list", [])
        ctx.preferred_sectors = result.get("preferred_sectors", [])
        ctx.trading_style = result.get("trading_style", "")
        ctx.notes = result.get("notes", "")


def _apply_thesis(ctx: InvestorContext, result) -> None:
    if isinstance(result, str):
        ctx.active_thesis_summary = result


def _apply_lessons(ctx: InvestorContext, result) -> None:
    if isinstance(result, str):
        ctx.recent_lessons = result


def _apply_portfolio(ctx: InvestorContext, result) -> None:
    if isinstance(result, str):
        ctx.portfolio_bias = result


def _apply_memory(ctx: InvestorContext, result) -> None:
    """Apply memory context block — NEW in V2."""
    if isinstance(result, str):
        ctx.memory_context_block = result


# ---------------------------------------------------------------------------
# Public render helper
# ---------------------------------------------------------------------------


def render_for_agent(ctx: InvestorContext) -> str:
    """Render InvestorContext as a text block for injection into AI prompts.

    Returns empty string if all fields are empty (new user, no data).
    """
    parts: list[str] = []

    if ctx.risk_appetite:
        parts.append(f"Risk appetite: {ctx.risk_appetite}")
    if ctx.trading_style:
        parts.append(f"Trading style: {ctx.trading_style}")
    if ctx.preferred_sectors:
        parts.append(f"Preferred sectors: {', '.join(ctx.preferred_sectors)}")
    if ctx.avoid_list:
        parts.append(f"Avoid list: {', '.join(ctx.avoid_list)}")
    if ctx.notes:
        parts.append(f"Investor notes: {ctx.notes}")
    if ctx.active_thesis_summary:
        parts.append(ctx.active_thesis_summary)
    if ctx.recent_lessons:
        parts.append(ctx.recent_lessons)
    if ctx.portfolio_bias:
        parts.append(ctx.portfolio_bias)

    # Memory block appended last — most contextual, highest recency signal
    if ctx.memory_context_block:
        parts.append(ctx.memory_context_block)

    if not parts:
        return ""
    return "[Investor context]\n" + "\n".join(parts)
