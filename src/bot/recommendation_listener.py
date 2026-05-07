"""
Recommendation Listener — Bot Segment, Wave 4

Subscribes to RecommendationReadyEvent on the global event bus.
Formats and sends Discord alerts to the configured channel.

Owner: bot segment. Adapter only — no domain logic.
Domain logic lives in ai/agents/proactive_alert_agent.py.

Wire-up: call RecommendationListener(bot).register() in app.py on_ready.
"""
from __future__ import annotations

import discord

from src.platform.config import settings
from src.platform.event_bus import get_event_bus
from src.platform.events import RecommendationReadyEvent
from src.platform.logging import get_logger

from .commands.recommendation_embeds import build_recommendation_embed

logger = get_logger(__name__)

# Urgency levels that get immediate push (vs. silent queue for batch briefing)
_PUSH_URGENCIES = {"NOW", "TODAY"}


class RecommendationListener:
    """
    Listens for RecommendationReadyEvent and pushes Discord messages.

    Lifecycle:
        listener = RecommendationListener(bot)
        listener.register()   # call once in on_ready, after bootstrap
    """

    def __init__(self, bot: discord.ext.commands.Bot) -> None:
        self._bot = bot
        self._registered = False

    def register(self) -> None:
        """Subscribe handler to the global event bus."""
        if self._registered:
            logger.warning("RecommendationListener already registered — skipping.")
            return
        get_event_bus().subscribe_handler(RecommendationReadyEvent, self._handle)
        self._registered = True
        logger.info("RecommendationListener registered on event bus.")

    # ── internal ───────────────────────────────────────────────────────────

    async def _handle(self, event: RecommendationReadyEvent) -> None:  # type: ignore[override]
        urgency = event.urgency.upper()

        # MONITORING urgency: skip immediate push, let briefing segment pick it up
        if urgency not in _PUSH_URGENCIES:
            logger.info(
                "recommendation_listener.deferred",
                symbol=event.symbol,
                action=event.action,
                urgency=urgency,
                reason="urgency_below_threshold",
            )
            return

        channel = await self._get_alert_channel()
        if channel is None:
            logger.error(
                "recommendation_listener.no_channel",
                symbol=event.symbol,
                hint="Set DISCORD_ALERT_CHANNEL_ID (or MORNING_CHANNEL_ID as fallback) in .env",
            )
            return

        # Wave 7: use rich embed — all content fields come from the event itself
        embed = build_recommendation_embed(event)

        try:
            await channel.send(embed=embed)
            logger.info(
                "recommendation_listener.sent",
                symbol=event.symbol,
                action=event.action,
                urgency=urgency,
                channel_id=channel.id,
                recommendation_id=event.recommendation_id,
                has_reasoning=bool(event.reasoning),
                has_thesis=bool(event.thesis_id),
            )
        except discord.DiscordException as exc:
            logger.exception(
                "recommendation_listener.send_failed",
                symbol=event.symbol,
                error=str(exc),
            )

    async def _get_alert_channel(
        self,
    ) -> discord.TextChannel | None:
        """Resolve the Discord alert channel via settings.alert_channel_id.

        Priority (defined in Settings): discord_alert_channel_id → morning_channel_id.
        Set DISCORD_ALERT_CHANNEL_ID in .env to use a dedicated alert channel;
        leave it blank to share the morning briefing channel.
        """
        channel_id_str = settings.alert_channel_id or None
        if not channel_id_str:
            return None

        try:
            channel_id = int(channel_id_str)
        except (TypeError, ValueError):
            logger.error("recommendation_listener.invalid_channel_id", value=channel_id_str)
            return None

        channel = self._bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(channel_id)
            except discord.NotFound:
                logger.error("recommendation_listener.channel_not_found", channel_id=channel_id)
                return None

        if not isinstance(channel, discord.TextChannel):
            logger.error(
                "recommendation_listener.not_text_channel",
                channel_id=channel_id,
                channel_type=type(channel).__name__,
            )
            return None

        return channel
