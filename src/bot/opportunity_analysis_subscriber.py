"""OpportunityAnalysisSubscriber — bot segment.

Owner: bot segment (adapter only). No domain logic.

Subscribes to OpportunityAnalysisCompletedEvent from ai segment and
sends a Discord embed to the alert channel.

Lifecycle:
    subscriber = OpportunityAnalysisSubscriber(bot)
    subscriber.register()   ← called in bot app.py on_ready
"""
from __future__ import annotations

import discord

from src.platform.config import settings
from src.platform.event_bus import get_event_bus
from src.platform.events import OpportunityAnalysisCompletedEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)


def _build_opportunity_embed(event: OpportunityAnalysisCompletedEvent) -> discord.Embed:
    """Build Discord embed from OpportunityAnalysisCompletedEvent."""
    # Colour based on overlap quality
    overlap_count = len(event.watchlist_overlap)
    colour = (
        discord.Color.green() if overlap_count >= 2
        else discord.Color.gold() if overlap_count == 1
        else discord.Color.light_grey()
    )

    title = f"🔍 Opportunity Screen — {event.trading_date or 'Today'}"
    embed = discord.Embed(
        title=title,
        description=event.verdict or "No significant overlap found.",
        colour=colour,
    )

    if event.ranked_tickers:
        embed.add_field(
            name="📊 Top Candidates",
            value=", ".join(f"**{t}**" for t in event.ranked_tickers),
            inline=False,
        )

    if event.watchlist_overlap:
        embed.add_field(
            name="✅ In Your Watchlist",
            value=", ".join(f"**{t}**" for t in event.watchlist_overlap),
            inline=True,
        )

    if event.thesis_relevant:
        embed.add_field(
            name="📋 Active Thesis",
            value=", ".join(f"**{t}**" for t in event.thesis_relevant),
            inline=True,
        )

    if event.action:
        embed.add_field(
            name="⚡ Action",
            value=event.action,
            inline=False,
        )

    if event.reasoning_summary:
        embed.add_field(
            name="💡 Reasoning",
            value=event.reasoning_summary[:500],
            inline=False,
        )

    confidence_pct = int(event.confidence * 100)
    embed.set_footer(text=f"Confidence {confidence_pct}%  ·  stock-agent opportunity screen")

    return embed


class OpportunityAnalysisSubscriber:
    """Push OpportunityAnalysisCompletedEvent to Discord alert channel."""

    def __init__(self, bot: discord.ext.commands.Bot) -> None:
        self._bot = bot
        self._registered = False

    def register(self) -> None:
        """Subscribe to EventBus. Safe to call multiple times."""
        if self._registered:
            return
        get_event_bus().subscribe_handler(OpportunityAnalysisCompletedEvent, self._handle)
        self._registered = True
        logger.info("opportunity_analysis_subscriber.registered")

    async def _handle(self, event: OpportunityAnalysisCompletedEvent) -> None:
        """Send embed to alert channel on OpportunityAnalysisCompletedEvent."""
        channel = await self._resolve_channel()
        if channel is None:
            logger.warning(
                "opportunity_analysis_subscriber.no_channel",
                hint="Set DISCORD_ALERT_CHANNEL_ID in .env",
            )
            return

        embed = _build_opportunity_embed(event)
        try:
            await channel.send(embed=embed)
            logger.info(
                "opportunity_analysis_subscriber.sent",
                verdict=event.verdict,
                ranked_tickers=list(event.ranked_tickers),
                channel_id=channel.id,
            )
        except discord.DiscordException as exc:
            logger.warning(
                "opportunity_analysis_subscriber.send_failed",
                error=str(exc),
            )

    async def _resolve_channel(self) -> discord.TextChannel | None:
        """Resolve alert channel from settings. Returns None when not configured."""
        channel_id_str = settings.alert_channel_id or None
        if not channel_id_str:
            return None
        try:
            channel_id = int(channel_id_str)
        except (TypeError, ValueError):
            return None

        channel = self._bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(channel_id)
            except discord.NotFound:
                return None

        return channel if isinstance(channel, discord.TextChannel) else None
