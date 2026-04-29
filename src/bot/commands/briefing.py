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
from src.briefing.formatter import format_eod_brief, format_morning_brief
from src.briefing.service import BriefingService
from src.platform.bootstrap import get_briefing_agent, get_quote_service
from src.platform.logging import get_logger
from src.watchlist.service import WatchlistService

logger = get_logger(__name__)

_SENTIMENT_COLOUR = {
    MarketSentiment.RISK_ON: discord.Color.green(),
    MarketSentiment.RISK_OFF: discord.Color.red(),
    MarketSentiment.MIXED: discord.Color.gold(),
    MarketSentiment.UNCERTAIN: discord.Color.greyple(),
}


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
                    session=session,
                )
                if phase == "morning":
                    brief: BriefOutput = await svc.generate_morning_brief(user_id=user_id)
                else:
                    brief = await svc.generate_eod_brief(user_id=user_id)
                await session.commit()
        except Exception as exc:
            logger.error("briefing.command.error", phase=phase, error=str(exc))
            await self.send_error(
                interaction,
                title="Brief generation failed",
                description=f"Could not generate {phase} brief.\n`{exc}`",
            )
            return

        embed = build_brief_embed(brief, phase=phase)
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
