"""PostMortemSubscriber — Discord delivery adapter for thesis post-mortem results.

Owner: bot segment (thin adapter — no business logic).

Responsibilities:
  - Subscribe to ThesisPostMortemReadyEvent
  - Build a Discord embed summarising the AI post-mortem
  - Send to decision_channel_id (falls back to morning_channel_id if not set)

Does NOT contain lesson extraction or memory logic.
"""

from __future__ import annotations

import datetime

import discord

from src.platform.config import settings
from src.platform.event_bus import get_event_bus
from src.platform.events import ThesisPostMortemReadyEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)

_VERDICT_COLOR = {
    "CORRECT": discord.Color.green(),
    "INCORRECT": discord.Color.red(),
    "MIXED": discord.Color.orange(),
    "INCONCLUSIVE": discord.Color.light_grey(),
}
_VERDICT_EMOJI = {
    "CORRECT": "✅",
    "INCORRECT": "❌",
    "MIXED": "🟡",
    "INCONCLUSIVE": "❓",
}


class PostMortemSubscriber:
    """Receive ThesisPostMortemReadyEvent → send Discord embed."""

    def __init__(self, channel_id: int | None = None) -> None:
        self._channel_id = channel_id
        self._client: discord.Client | None = None

    def set_client(self, client: discord.Client) -> None:
        self._client = client

    def register(self) -> None:
        bus = get_event_bus()
        bus.subscribe(ThesisPostMortemReadyEvent, self._handle)
        logger.info("post_mortem_subscriber.registered", channel_id=self._channel_id)

    async def _handle(self, event: ThesisPostMortemReadyEvent) -> None:
        if not self._client or not self._channel_id:
            logger.warning(
                "post_mortem_subscriber.no_client_or_channel",
                thesis_id=event.thesis_id,
            )
            return

        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            logger.warning(
                "post_mortem_subscriber.channel_not_found",
                channel_id=self._channel_id,
            )
            return

        try:
            embed = _build_embed(event)
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "post_mortem_subscriber.sent",
                thesis_id=event.thesis_id,
                ticker=event.ticker,
                verdict=event.verdict,
            )
        except Exception as exc:
            logger.error(
                "post_mortem_subscriber.send_error",
                thesis_id=event.thesis_id,
                error=str(exc),
            )


def _build_embed(event: ThesisPostMortemReadyEvent) -> discord.Embed:
    verdict_emoji = _VERDICT_EMOJI.get(event.verdict, "❓")
    color = _VERDICT_COLOR.get(event.verdict, discord.Color.light_grey())
    pnl_str = (
        f"{event.outcome_pnl_pct:+.1f}%"
        if event.outcome_pnl_pct is not None
        else "N/A"
    )
    confidence_bar = "█" * round(event.confidence * 10) + "░" * (10 - round(event.confidence * 10))
    tags_str = " · ".join(f"`{t}`" for t in event.memory_tags) if event.memory_tags else "—"
    date_str = datetime.datetime.now(tz=datetime.UTC).strftime("%d/%m/%Y %H:%M UTC")

    embed = discord.Embed(
        title=f"{verdict_emoji} Post-Mortem: {event.ticker}",
        description=f"**{event.thesis_title}**",
        color=color,
        timestamp=datetime.datetime.now(tz=datetime.UTC),
    )
    embed.add_field(name="Verdict", value=f"{verdict_emoji} {event.verdict}", inline=True)
    embed.add_field(name="P&L", value=pnl_str, inline=True)
    embed.add_field(name="Close reason", value=event.close_reason, inline=True)
    embed.add_field(
        name="📚 Bài học",
        value=event.lesson or "—",
        inline=False,
    )
    if event.pattern:
        embed.add_field(name="Pattern", value=f"`{event.pattern}`", inline=True)
    embed.add_field(
        name=f"Confidence [{confidence_bar}]",
        value=f"{event.confidence:.0%}",
        inline=True,
    )
    embed.add_field(name="Memory tags", value=tags_str, inline=False)
    embed.set_footer(text=f"stock-agent · {date_str}")
    return embed
