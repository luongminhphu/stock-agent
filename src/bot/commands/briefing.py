"""Briefing commands cog.

Owner: bot segment.
Adapter only: parse Discord interaction → call BriefingService → format via briefing.formatter.

NO business logic here. BriefingService owns the flow.
formatter.py owns the string rendering.
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.ai.schemas import BriefOutput, MarketSentiment
from src.bot.commands.base import BaseCog
from src.briefing.agenda_cache import get_agenda
from src.briefing.formatter import build_brief_pages, format_eod_brief, format_morning_brief
from src.briefing.service import BriefingService
from src.platform.bootstrap import (
    get_agenda_service_factory,
    get_briefing_agent,
    get_pnl_service_class,
    get_quote_service,
    get_sector_rotation_agent,
)
from src.platform.db import get_session
from src.platform.logging import get_logger
from src.watchlist.service import WatchlistService

logger = get_logger(__name__)

_SENTIMENT_COLOUR = {
    MarketSentiment.RISK_ON:   discord.Color.green(),
    MarketSentiment.RISK_OFF:  discord.Color.red(),
    MarketSentiment.MIXED:     discord.Color.gold(),
    MarketSentiment.UNCERTAIN: discord.Color.greyple(),
    # Legacy fallbacks
    MarketSentiment.BULLISH:   discord.Color.green(),
    MarketSentiment.BEARISH:   discord.Color.red(),
    MarketSentiment.NEUTRAL:   discord.Color.greyple(),
}

_OUTCOME_LABEL = {
    "acted": "\u2705 \u0110\u00e3 th\u1ef1c hi\u1ec7n",
    "watching": "\U0001f440 \u0110ang theo d\u00f5i",
    "skipped": "\u23ed Skip h\u00f4m nay",
}


class BriefFeedbackView(discord.ui.View):
    """Discord View attached to a brief embed for capturing user outcome.

    Three buttons: acted / watching / skipped.
    On click: persist BriefFeedback via BriefingService, send ephemeral confirm.
    Timeout: 4 hours — covers a full trading session.
    """

    def __init__(self, snapshot_id: int, user_id: str) -> None:
        super().__init__(timeout=14400)  # 4h
        self._snapshot_id = snapshot_id
        self._user_id = user_id

    @discord.ui.button(label="\u2705 \u0110\u00e3 th\u1ef1c hi\u1ec7n", style=discord.ButtonStyle.success)
    async def btn_acted(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._record(interaction, outcome="acted")

    @discord.ui.button(label="\U0001f440 \u0110ang theo d\u00f5i", style=discord.ButtonStyle.secondary)
    async def btn_watching(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._record(interaction, outcome="watching")

    @discord.ui.button(label="\u23ed Skip h\u00f4m nay", style=discord.ButtonStyle.danger)
    async def btn_skipped(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._record(interaction, outcome="skipped")

    async def _record(self, interaction: discord.Interaction, outcome: str) -> None:
        """Persist feedback and send ephemeral confirmation."""
        try:
            async with get_session() as session:
                svc = BriefingService(
                    watchlist_service=WatchlistService(session=session),
                    quote_service=get_quote_service(),
                    briefing_agent=get_briefing_agent(),
                    session=session,
                )
                await svc.record_feedback(
                    brief_snapshot_id=self._snapshot_id,
                    user_id=self._user_id,
                    outcome=outcome,
                )
        except Exception as exc:
            logger.error(
                "briefing.feedback_view.error",
                snapshot_id=self._snapshot_id,
                outcome=outcome,
                error=str(exc),
            )

        label = _OUTCOME_LABEL.get(outcome, outcome)
        await interaction.response.send_message(
            f"Ghi nh\u1eadn: **{label}**", ephemeral=True
        )
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.message.edit(view=self)  # type: ignore[union-attr]
        self.stop()


class BriefingCog(BaseCog):
    """Slash commands for market briefs."""

    @app_commands.command(name="morning_brief", description="Generate your morning market brief")
    async def morning_brief(self, interaction: discord.Interaction) -> None:
        await self._run_brief(interaction, phase="morning")

    @app_commands.command(name="eod_brief", description="Generate your end-of-day market brief")
    async def eod_brief(self, interaction: discord.Interaction) -> None:
        await self._run_brief(interaction, phase="eod")

    async def _run_brief(self, interaction: discord.Interaction, phase: str) -> None:
        await interaction.response.defer(ephemeral=False)
        user_id = self.user_id(interaction)

        try:
            async with self.db_session() as session:
                # Lazy imports — session-scoped services, not singletons
                from src.thesis.service import ThesisService
                from src.readmodel.dashboard_service import DashboardService
                from src.platform.investor_profile import InvestorProfileService

                # agenda_service: init via factory, fail-safe
                agenda_service = None
                agenda_factory = get_agenda_service_factory()
                if agenda_factory is not None:
                    try:
                        agenda_service = agenda_factory(session)
                    except Exception as _e:
                        logger.warning("briefing.command.agenda_service_init_failed", error=str(_e))

                # lesson_service: LessonService is stateless (all methods static);
                # BriefingService._build_lessons_context guards on truthy check,
                # so passing a bare sentinel object with _session stored is enough.
                # We pass the actual LessonService class so the guard is truthy
                # and the static call inside _build_lessons_context resolves correctly.
                from src.ai.memory.lesson_service import LessonService

                svc = BriefingService(
                    watchlist_service=WatchlistService(session=session),
                    quote_service=get_quote_service(),
                    briefing_agent=get_briefing_agent(),
                    pnl_service=get_pnl_service_class()(
                        session=session,
                        quote_service=get_quote_service(),
                    ),
                    thesis_service=ThesisService(session=session),
                    dashboard_service=DashboardService(session=session),
                    agenda_service=agenda_service,
                    sector_agent=get_sector_rotation_agent(),
                    lesson_service=LessonService,          # stateless — class reference is sentinel
                    investor_profile_service=InvestorProfileService(session=session),
                    session=session,
                )
                if phase == "morning":
                    brief_result = await svc.generate_morning_brief(user_id=user_id)
                else:
                    brief_result = await svc.generate_eod_brief(user_id=user_id)
                await session.commit()
        except Exception as exc:
            logger.error("briefing.command.error", phase=phase, error=str(exc))
            await self.send_error(
                interaction,
                title="Brief generation failed",
                description=f"Could not generate {phase} brief.\n`{exc}`",
            )
            return

        embeds = build_brief_embeds(brief_result.output, phase=phase)

        # P1.5: prepend cached Daily Agenda block to first embed when available
        # so manual /morning_brief and scheduler-based Morning Brief share
        # the same visual anchor around DECIDE/WATCH/DEFER.
        agenda_block = get_agenda(user_id)
        if agenda_block and embeds:
            first = embeds[0]
            original_desc = first.description or ""
            if original_desc:
                first.description = f"{agenda_block}\n\n{original_desc}"
            else:
                first.description = agenda_block

        # Discord enforces a 6000-char *total* limit per message across all embeds.
        # Send each embed as its own message to avoid the limit entirely.
        # BriefFeedbackView is attached only to the last message.
        for i, embed in enumerate(embeds):
            is_last = i == len(embeds) - 1
            if is_last and brief_result.snapshot_id is not None:
                view = BriefFeedbackView(
                    snapshot_id=brief_result.snapshot_id,
                    user_id=user_id,
                )
                await interaction.followup.send(embed=embed, view=view, ephemeral=False)
            else:
                await interaction.followup.send(embed=embed, ephemeral=False)


def build_brief_embeds(brief: BriefOutput, phase: str) -> list[discord.Embed]:
    """Convert BriefOutput → list[discord.Embed], one embed per page.

    Page 1 gets the title and accent colour.
    Continuation pages get a minimal footer-only embed so Discord renders
    them as a clean continuation rather than identical headers.

    Public — importable by scheduler and other bot adapters.
    """
    title = "\U0001f305 Morning Brief" if phase == "morning" else "\U0001f307 End-of-Day Brief"
    colour = _SENTIMENT_COLOUR.get(brief.sentiment, discord.Color.blurple())

    pages = build_brief_pages(
        brief,
        brief_type="Morning Brief" if phase == "morning" else "EOD Brief",
    )

    embeds: list[discord.Embed] = []
    for i, page in enumerate(pages):
        if i == 0:
            embed = discord.Embed(
                title=title,
                description=page,
                color=colour,
            )
        else:
            embed = discord.Embed(
                description=page,
                color=discord.Color.blurple(),
            )
        embed.set_footer(text="stock-agent \u00b7 AI-native" + (f" ({i + 1}/{len(pages)})" if len(pages) > 1 else ""))
        embeds.append(embed)

    return embeds


# ---------------------------------------------------------------------------
# Legacy single-embed builder — kept for scheduler backward compat
# ---------------------------------------------------------------------------

def build_brief_embed(brief: BriefOutput, phase: str) -> discord.Embed:
    """Single-embed builder — kept for backward compatibility with scheduler.

    Prefer build_brief_embeds() for new callers.
    """
    return build_brief_embeds(brief, phase=phase)[0]
