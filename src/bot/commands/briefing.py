"""Briefing commands cog.

Owner: bot segment.
Adapter only: parse Discord interaction → call BriefingService → format via briefing.formatter.

NO business logic here. BriefingService owns the flow.
formatter.py owns the string rendering.
"""

from __future__ import annotations

import discord
from discord import app_commands
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.schemas import BriefOutput, MarketSentiment
from src.bot.commands.base import BaseCog
from src.briefing.formatter import format_eod_brief, format_morning_brief
from src.briefing.service import BriefingService
from src.platform.bootstrap import get_briefing_agent, get_pnl_service, get_quote_service
from src.platform.db import async_session_factory
from src.platform.logging import get_logger
from src.watchlist.service import WatchlistService

logger = get_logger(__name__)

_SENTIMENT_COLOUR = {
    MarketSentiment.RISK_ON: discord.Color.green(),
    MarketSentiment.RISK_OFF: discord.Color.red(),
    MarketSentiment.MIXED: discord.Color.gold(),
    MarketSentiment.UNCERTAIN: discord.Color.greyple(),
}

_OUTCOME_LABEL = {
    "acted": "✅ Đã thực hiện",
    "watching": "👀 Đang theo dõi",
    "skipped": "⏭ Skip hôm nay",
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

    @discord.ui.button(label="✅ Đã thực hiện", style=discord.ButtonStyle.success)
    async def btn_acted(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._record(interaction, outcome="acted")

    @discord.ui.button(label="👀 Đang theo dõi", style=discord.ButtonStyle.secondary)
    async def btn_watching(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._record(interaction, outcome="watching")

    @discord.ui.button(label="⏭ Skip hôm nay", style=discord.ButtonStyle.danger)
    async def btn_skipped(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._record(interaction, outcome="skipped")

    async def _record(self, interaction: discord.Interaction, outcome: str) -> None:
        """Persist feedback and send ephemeral confirmation."""
        try:
            async with async_session_factory() as session:
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
            f"Ghi nhận: **{label}**", ephemeral=True
        )
        # Disable all buttons after response to prevent double-click
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
                svc = BriefingService(
                    watchlist_service=WatchlistService(session=session),
                    quote_service=get_quote_service(),
                    briefing_agent=get_briefing_agent(),
                    pnl_service=get_pnl_service()(session),
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

        embed = build_brief_embed(brief_result.output, phase=phase)

        # Attach feedback buttons only when snapshot was persisted
        if brief_result.snapshot_id is not None:
            view = BriefFeedbackView(
                snapshot_id=brief_result.snapshot_id,
                user_id=user_id,
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=False)
        else:
            await interaction.followup.send(embed=embed, ephemeral=False)


def build_brief_embed(brief: BriefOutput, phase: str) -> discord.Embed:
    """Convert BriefOutput → Discord Embed.

    Uses briefing.formatter for the text body, then wraps in Embed chrome.
    Public — importable by scheduler and other bot adapters.
    """
    title = "\U0001f305 Morning Brief" if phase == "morning" else "\U0001f307 End-of-Day Brief"
    colour = _SENTIMENT_COLOUR.get(brief.sentiment, discord.Color.blurple())
    formatted_text = format_morning_brief(brief) if phase == "morning" else format_eod_brief(brief)
    embed = discord.Embed(
        title=title,
        description=formatted_text[:4096],
        color=colour,
    )
    embed.set_footer(text="stock-agent · AI-native")
    return embed
