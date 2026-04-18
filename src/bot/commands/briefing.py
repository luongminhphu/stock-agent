"""Briefing commands cog.

Owner: bot segment.
Adapter only: parse Discord interaction → call BriefingService → format embed.
"""
from __future__ import annotations

import discord
from discord import app_commands

from src.bot.commands.base import BaseCog
from src.briefing.models import MarketBrief
from src.briefing.service import BriefingService
from src.platform.bootstrap import get_briefing_agent, get_quote_service
from src.platform.logging import get_logger
from src.watchlist.service import WatchlistService

logger = get_logger(__name__)


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
                )
                if phase == "morning":
                    brief = await svc.generate_morning_brief(user_id=user_id)
                else:
                    brief = await svc.generate_eod_brief(user_id=user_id)
        except Exception as exc:
            logger.error("briefing.command.error", phase=phase, error=str(exc))
            await self.send_error(
                interaction,
                title="Brief generation failed",
                description=f"Could not generate {phase} brief.\n`{exc}`",
            )
            return

        await interaction.followup.send(embed=_build_brief_embed(brief, phase=phase), ephemeral=False)


def _build_brief_embed(brief: MarketBrief, phase: str) -> discord.Embed:
    title = "🌅 Morning Brief" if phase == "morning" else "🌇 End-of-Day Brief"
    colour = discord.Color.gold() if phase == "morning" else discord.Color.orange()

    embed = discord.Embed(
        title=title,
        description=brief.summary[:1000] if brief.summary else "No summary available.",
        color=colour,
    )

    if brief.market_view:
        embed.add_field(name="Market View", value=brief.market_view[:500], inline=False)
    if brief.watchlist_focus:
        embed.add_field(name="Watchlist Focus", value=brief.watchlist_focus[:500], inline=False)
    if brief.action_items:
        action_text = "\n".join(f"• {item}" for item in brief.action_items[:5])
        embed.add_field(name="Action Items", value=action_text, inline=False)

    ts = getattr(brief, "generated_at", None)
    ts_str = ts.strftime("%H:%M %d/%m/%Y") if ts else "N/A"
    embed.set_footer(text=f"Generated at {ts_str} · stock-agent")
    return embed
