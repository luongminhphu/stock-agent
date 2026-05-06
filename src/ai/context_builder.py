"""ContextBuilder — assembles investor context for AI agent calls.

Owner: ai segment.
Callers: ai/agents/*.py only.

Builds an InvestorContext from:
  - platform.investor_profile (static profile + risk settings)
  - thesis segment (ThesisHealthSnapshot — typed, urgency-aware)  ← V3 upgrade
  - thesis.lesson_service (recent decision lessons)
  - portfolio segment (portfolio bias — sector exposure, P&L tilt)
  - market.registry (sector key_metrics per position ticker)  ← V2-3
  - ai.memory (MemoryContext — episodic + semantic memory)    ← V2

Boundary rule:
  ContextBuilder knows ABOUT domain segments but does NOT own their logic.
  It calls their public read APIs; never writes to them.

Backward compatibility:
  ContextBuilder(session) still works exactly as before.
  memory injection is additive — if MemoryService fails, context is still built.
  sector context injection is additive — if registry fails, context is still built.
  thesis health injection replaces the old flat summary — same slot in
  InvestorContext.active_thesis_summary; zero signature change for agents.

Wave 3 fix:
  _fetch_investor_profile() now calls svc.get_profile(user_id=user_id) which
  exists on InvestorProfileService. Previously called svc.get_profile() on a
  method that did not exist → always raised AttributeError → profile block
  was always empty despite data being available in DB.

V3 (ThesisHealthSnapshot):
  _fetch_thesis_summary() replaced by _fetch_thesis_health().
  Produces urgency-sorted, stop-loss-proximity-aware block instead of a
  flat text dump. Falls back to old summary method if import fails.
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

    # From thesis segment (V3: ThesisHealthSnapshot formatted block)
    active_thesis_summary: str = ""

    # From thesis.lesson_service
    recent_lessons: str = ""

    # From portfolio segment + market.registry (V2-3)
    portfolio_bias: str = ""

    # From ai.memory (V2)
    memory_context_block: str = ""


class ContextBuilder:
    """Assembles InvestorContext by querying domain segment read-APIs.

    Usage::

        ctx = await ContextBuilder(session).build(user_id="user_001")
        profile_str = render_for_agent(ctx)
    """

    def __init__(self, session: "AsyncSession") -> None:
        self._session = session

    async def build(self, user_id: str | None = None) -> InvestorContext:
        """Fetch all context layers concurrently and assemble InvestorContext.

        All sub-fetches are fire-and-forget — if any fails, the rest still
        succeed. ContextBuilder never blocks AI calls due to data errors.
        """
        results = await asyncio.gather(
            self._fetch_investor_profile(user_id),
            self._fetch_thesis_health(user_id),
            self._fetch_recent_lessons(user_id),
            self._fetch_portfolio_bias(user_id),
            self._fetch_memory_context(user_id),
            return_exceptions=True,
        )

        ctx = InvestorContext()
        _apply_profile(ctx, results[0])
        _apply_thesis(ctx, results[1])
        _apply_lessons(ctx, results[2])
        _apply_portfolio(ctx, results[3])
        _apply_memory(ctx, results[4])

        return ctx

    # ------------------------------------------------------------------
    # Private fetch methods — each returns a plain value or raises
    # ------------------------------------------------------------------

    async def _fetch_investor_profile(self, user_id: str | None) -> dict:
        """Fetch investor profile dict from InvestorProfileService.

        Wave 3: calls svc.get_profile(user_id=user_id) — the correct
        public method that returns a ContextBuilder-compatible dict.
        """
        try:
            from src.platform.investor_profile import InvestorProfileService

            svc = InvestorProfileService(self._session)
            profile = await svc.get_profile(user_id=user_id)
            return profile if isinstance(profile, dict) else {}
        except Exception as exc:
            logger.warning("context_builder.investor_profile_failed", error=str(exc))
            return {}

    async def _fetch_thesis_health(self, user_id: str | None) -> str:
        """Build thesis context using ThesisHealthSnapshot (V3).

        Returns urgency-sorted, stop-loss-proximity-aware thesis block.
        Falls back to flat summary if ThesisHealthSnapshot is unavailable.
        Never raises — thesis failure must not block AI calls.
        """
        if not user_id:
            return ""
        try:
            from src.thesis.health_snapshot import build_thesis_health_snapshots

            snapshots = await build_thesis_health_snapshots(
                self._session, user_id=user_id
            )
            if not snapshots:
                return ""

            lines = [f"Thesis health ({len(snapshots)} active):"]
            for snap in snapshots:
                lines.append(f"  {snap.format_for_prompt()}")

            # Urgency summary line — draw agent's attention to AT_RISK
            at_risk = [s for s in snapshots if s.urgency_flag in ("AT_RISK", "INVALIDATED")]
            review_due = [s for s in snapshots if s.urgency_flag == "REVIEW_DUE"]
            if at_risk:
                tickers = ", ".join(s.ticker for s in at_risk)
                lines.append(
                    f"  ⚠️  AT_RISK/INVALIDATED: {tickers} — "
                    f"xem xét ngay, có thể cần action."
                )
            if review_due:
                tickers = ", ".join(s.ticker for s in review_due)
                lines.append(f"  🔔 Cần review: {tickers}")

            return "\n".join(lines)

        except Exception as exc:
            logger.warning(
                "context_builder.thesis_health_failed",
                user_id=user_id,
                error=str(exc),
            )
            # Fallback to old flat summary on import/runtime error
            return await self._fetch_thesis_summary_fallback(user_id)

    async def _fetch_thesis_summary_fallback(self, user_id: str | None) -> str:
        """Legacy flat thesis summary — used as fallback only."""
        try:
            from src.thesis.service import ThesisService

            svc = ThesisService(self._session)
            theses = await svc.list_active(user_id=user_id)
            if not theses:
                return ""
            lines = []
            for t in theses[:5]:
                ticker = getattr(t, "ticker", "?")
                direction = getattr(t, "direction", "")
                summary = getattr(t, "summary", "") or ""
                line = f"  - {ticker}"
                if direction:
                    line += f" ({direction})"
                if summary:
                    line += f": {summary[:120]}"
                lines.append(line)
            return "Active theses:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("context_builder.thesis_summary_fallback_failed", error=str(exc))
            return ""

    async def _fetch_recent_lessons(self, user_id: str | None) -> str:
        try:
            from src.thesis.lesson_service import LessonService

            svc = LessonService(self._session)
            lessons = await svc.get_recent(user_id=user_id, limit=3)
            if not lessons:
                return ""
            lines = []
            for les in lessons:
                text = getattr(les, "lesson_text", "") or ""
                if text:
                    lines.append(f"  - {text[:150]}")
            if not lines:
                return ""
            return "Recent lessons:\n" + "\n".join(lines)
        except Exception as exc:
            logger.warning("context_builder.recent_lessons_failed", error=str(exc))
            return ""

    async def _fetch_portfolio_bias(self, user_id: str | None) -> str:
        """Build portfolio bias string with P&L details + sector key_metrics.

        V2-3 changes:
          - Uses port_ctx.format_for_prompt() instead of str(port_ctx)
            to avoid raw dataclass repr leaking into AI prompts.
          - Injects sector key_metrics from market.registry per ticker
            (deduped by sector string, capped at 5 distinct sectors).
        """
        try:
            from src.market.registry import registry
            from src.portfolio import get_portfolio_context

            port_ctx = await get_portfolio_context(self._session, user_id)
            if not port_ctx.has_positions:
                return ""

            base = port_ctx.format_for_prompt()

            # Inject sector key_metrics from market/registry (V2-1)
            sector_blocks: list[str] = []
            seen_contexts: set[str] = set()
            for pos in port_ctx.open_positions:
                ctx_str = registry.get_sector_context_str(pos.ticker)
                if ctx_str and ctx_str not in seen_contexts:
                    seen_contexts.add(ctx_str)
                    sector_blocks.append(ctx_str)
                    if len(sector_blocks) >= 5:  # cap to avoid prompt bloat
                        break

            if sector_blocks:
                base += "\n\nSector context:\n" + "\n".join(
                    f"  {s}" for s in sector_blocks
                )

            return base
        except Exception as exc:
            logger.warning("context_builder.portfolio_bias_failed", error=str(exc))
            return ""

    async def _fetch_memory_context(self, user_id: str | None) -> str:
        """Fetch episodic + semantic memory for the user.

        Returns empty string if user_id is None or memory is unavailable.
        Never raises — memory failure must not block AI calls.
        """
        if not user_id:
            return ""
        try:
            from src.ai.memory.memory_service import MemoryService

            mem_ctx = await MemoryService.get_memory_context(
                self._session, user_id=user_id
            )
            if mem_ctx.is_empty():
                return ""
            return mem_ctx.render()
        except Exception as exc:
            logger.warning("context_builder.memory_context_failed", error=str(exc))
            return ""


# ---------------------------------------------------------------------------
# Apply helpers — each applies one result slot into InvestorContext
# ---------------------------------------------------------------------------

def _apply_profile(ctx: InvestorContext, result: object) -> None:
    if isinstance(result, dict) and result:
        ctx.risk_appetite = result.get("risk_appetite", "")
        ctx.avoid_list = result.get("avoid_list", [])
        ctx.preferred_sectors = result.get("preferred_sectors", [])
        ctx.trading_style = result.get("trading_style", "")
        ctx.notes = result.get("notes", "")


def _apply_thesis(ctx: InvestorContext, result: object) -> None:
    if isinstance(result, str):
        ctx.active_thesis_summary = result


def _apply_lessons(ctx: InvestorContext, result: object) -> None:
    if isinstance(result, str):
        ctx.recent_lessons = result


def _apply_portfolio(ctx: InvestorContext, result: object) -> None:
    if isinstance(result, str):
        ctx.portfolio_bias = result


def _apply_memory(ctx: InvestorContext, result: object) -> None:
    if isinstance(result, str):
        ctx.memory_context_block = result


# ---------------------------------------------------------------------------
# render_for_agent — formats InvestorContext for prompt injection
# ---------------------------------------------------------------------------

def render_for_agent(ctx: InvestorContext) -> str:
    """Render InvestorContext into a structured prompt block.

    Called by agents just before constructing their system prompt.
    Returns empty string if context is fully empty (no-op for agents).
    """
    parts: list[str] = []

    # Investor profile
    profile_lines: list[str] = []
    if ctx.risk_appetite:
        profile_lines.append(f"  Kh\u1ea9u v\u1ecb r\u1ee7i ro: {ctx.risk_appetite}")
    if ctx.trading_style:
        profile_lines.append(f"  Trading style: {ctx.trading_style}")
    if ctx.preferred_sectors:
        profile_lines.append(f"  Ng\u00e0nh \u01b0u ti\u00ean: {', '.join(ctx.preferred_sectors)}")
    if ctx.avoid_list:
        profile_lines.append(f"  Tr\u00e1nh: {', '.join(ctx.avoid_list)}")
    if ctx.notes:
        profile_lines.append(f"  Ghi ch\u00fa: {ctx.notes}")
    if profile_lines:
        parts.append("[Investor profile]\n" + "\n".join(profile_lines))

    # Active theses (V3: ThesisHealthSnapshot block)
    if ctx.active_thesis_summary:
        parts.append(f"[Thesis health]\n{ctx.active_thesis_summary}")

    # Recent lessons
    if ctx.recent_lessons:
        parts.append(f"[{ctx.recent_lessons}]")

    # Portfolio — V2-3: formatted with sector context injected
    if ctx.portfolio_bias:
        parts.append(f"[Portfolio hi\u1ec7n t\u1ea1i]\n{ctx.portfolio_bias}")

    # Memory — episodic + semantic
    if ctx.memory_context_block:
        parts.append(ctx.memory_context_block)

    return "\n\n".join(parts)
