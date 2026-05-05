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

    async with AsyncSessionLocal() as session:
        ctx = await ContextBuilder(session).build()
    prompt_block = render_for_agent(ctx)
    # inject prompt_block into agent call
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

    source_flags: dict mapping source name → bool, records which
    upstream sources were available at build time. Used for logging
    and for agents to adjust confidence when data is missing.
    """

    investor_profile_block: str = ""
    active_thesis_summary: str = ""
    recent_lessons: str = ""
    portfolio_bias: str = ""
    watchlist_signals: str = ""  # reserved — Wave 3
    built_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_flags: dict[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class ContextBuilder:
    """Assembles InvestorContext for a single AI call.

    Each ``_fetch_*`` method is fully independent and silently degrades
    to empty string on any exception. This ensures ContextBuilder never
    raises and never blocks an AI call due to a missing data source.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(self) -> InvestorContext:
        """Build and return InvestorContext.

        Runs all fetchers concurrently where possible; aggregates results
        into InvestorContext. Each failed source is logged at WARNING level
        and recorded in source_flags as False.
        """
        ctx = InvestorContext()

        # --- investor profile (platform segment) ---
        ctx.investor_profile_block, ctx.source_flags["investor_profile"] = (
            await self._fetch_investor_profile()
        )

        # --- active theses (thesis segment) ---
        ctx.active_thesis_summary, ctx.source_flags["active_theses"] = (
            await self._fetch_active_theses()
        )

        # --- recent lessons (thesis segment) ---
        ctx.recent_lessons, ctx.source_flags["recent_lessons"] = (
            await self._fetch_recent_lessons()
        )

        # --- portfolio bias (portfolio segment) ---
        ctx.portfolio_bias, ctx.source_flags["portfolio_bias"] = (
            await self._fetch_portfolio_bias()
        )

        logger.info(
            "context_builder.built",
            sources_available=sum(ctx.source_flags.values()),
            sources_total=len(ctx.source_flags),
            built_at=ctx.built_at.isoformat(),
        )
        return ctx

    # ------------------------------------------------------------------
    # Private fetchers — each returns (str, bool) = (content, available)
    # ------------------------------------------------------------------

    async def _fetch_investor_profile(self) -> tuple[str, bool]:
        """Read latest InvestorProfile snapshot and return summary_for_ai.

        Falls back to StaticProfile.from_settings() when no DB snapshot
        exists yet (first run before scheduler builds initial snapshot).
        """
        try:
            from src.platform.investor_profile import (
                InvestorProfileService,
                StaticProfile,
            )

            svc = InvestorProfileService(session=self._session)
            snapshot = await svc.get_latest()
            if snapshot and snapshot.summary_for_ai:
                return snapshot.summary_for_ai, True

            # Fallback: render static profile from settings
            static = StaticProfile.from_settings()
            rendered = static.render_for_prompt()
            if rendered:
                logger.info(
                    "context_builder.investor_profile_fallback_static",
                    reason="no_db_snapshot",
                )
                return rendered, True
            return "", False
        except Exception as exc:
            logger.warning("context_builder.investor_profile_error", error=str(exc))
            return "", False

    async def _fetch_active_theses(self) -> tuple[str, bool]:
        """Summarise active theses: ticker, entry_price, stop_loss, target, status."""
        try:
            from sqlalchemy import select

            from src.thesis.models import Thesis

            result = await self._session.execute(
                select(Thesis)
                .where(Thesis.status == "act