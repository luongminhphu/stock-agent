"""DiscordBriefDelivery — adapter gửi Morning/EOD Brief lên Discord channel.

Owner: bot segment (boundary fix B5).

Trước đây logic này nằm trong ``briefing.briefing_listener`` (domain import
``bot.commands.briefing`` để build embed → vi phạm domain-no-adapters). Giờ
briefing chỉ generate và gọi ``BriefDelivery`` protocol; bot quyết định
embed/channel/client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.platform.logging import get_logger

if TYPE_CHECKING:
    import discord

logger = get_logger(__name__)


class DiscordBriefDelivery:
    """Implement ``src.briefing.briefing_listener.BriefDelivery`` cho Discord."""

    def __init__(self, morning_channel_id: int | None, eod_channel_id: int | None) -> None:
        self._client: discord.Client | None = None
        self._channels = {"morning": morning_channel_id, "eod": eod_channel_id}

    def set_client(self, client: discord.Client) -> None:
        """Inject discord.Client sau khi bot login (bot on_ready)."""
        self._client = client
        logger.info("brief_delivery.client_injected")

    def is_ready(self, phase: str) -> bool:
        """True nếu có client + channel cho phase — listener bỏ qua generate nếu False."""
        if self._client is None:
            logger.warning(
                "brief_delivery.no_client",
                phase=phase,
                reason="discord_client not injected yet — call set_client() in on_ready",
            )
            return False
        if not self._channels.get(phase):
            logger.warning("brief_delivery.no_channel", phase=phase)
            return False
        return True

    async def deliver(self, brief: Any, *, phase: str, agenda_summary: str | None = None) -> None:
        from src.bot.commands.briefing import build_brief_embed

        channel_id = self._channels.get(phase)
        channel = self._client.get_channel(channel_id) if self._client and channel_id else None
        if channel is None:
            logger.warning("brief_delivery.channel_not_found", channel_id=channel_id, phase=phase)
            return

        embed = build_brief_embed(brief, phase=phase)
        # P1/P1.5: prepend agenda summary (DECIDE/WATCH/DEFER) lên đầu description.
        if agenda_summary:
            original = embed.description or ""
            embed.description = f"{agenda_summary}\n\n{original}" if original else agenda_summary

        await channel.send(embed=embed)  # type: ignore[union-attr]
        logger.info("brief_delivery.sent", phase=phase, channel_id=channel_id)
