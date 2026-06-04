"""
Invalidation subscriber — bot segment.

Subscribes ThesisInvalidatedEvent and pushes a Discord embed
so the owner is notified immediately when a thesis is invalidated.

Owner: bot segment (thin adapter — no domain logic).
Emitter: thesis/thesis_review_listener.py
"""
from __future__ import annotations

import discord

from src.platform.event_bus import get_event_bus
from src.platform.events import ThesisInvalidatedEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Colour thresholds — mirror INVALIDATION_THRESHOLD in thesis_review_listener.py
_COLOR_CRITICAL = 0xE74C3C   # red    — score >= 0.90 (INVALIDATED verdict)
_COLOR_HIGH     = 0xE67E22   # orange — score >= 0.75 (BEARISH verdict)
_COLOR_WARN     = 0xF1C40F   # yellow — below 0.90 but still triggered

_TRIGGER_DESC_MAX = 300


class InvalidationSubscriber:
    """Receives ThesisInvalidatedEvent → Discord embed for owner.

    Embed structure:
        Title   : 🚨 Thesis invalidated — {SYMBOL}
        Colour  : red (≥0.90) / orange (≥0.75) / yellow (< 0.90)
        Fields  : symbol, invalidation_score, trigger_description
        Footer  : thesis_id + event_id
    """

    def __init__(self, channel_id: int) -> None:
        self._channel_id = channel_id
        self._client: discord.Client | None = None

    def set_client(self, client: discord.Client) -> None:
        self._client = client

    def register(self) -> None:
        bus = get_event_bus()
        bus.subscribe(ThesisInvalidatedEvent, self._handle)
        logger.info("invalidation_subscriber.registered", channel_id=self._channel_id)

    async def _handle(self, event: ThesisInvalidatedEvent) -> None:
        if self._client is None:
            logger.warning("invalidation_subscriber.no_client")
            return

        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            logger.warning(
                "invalidation_subscriber.channel_not_found",
                channel_id=self._channel_id,
            )
            return

        embed = self._build_embed(event)
        try:
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.warning(
                "invalidation_subscriber.sent",
                symbol=event.symbol,
                thesis_id=event.thesis_id,
                invalidation_score=event.invalidation_score,
            )
        except Exception as exc:
            logger.exception("invalidation_subscriber.send_failed", error=str(exc))

    def _build_embed(self, event: ThesisInvalidatedEvent) -> discord.Embed:
        score = event.invalidation_score
        if score >= 0.90:
            colour = _COLOR_CRITICAL
        elif score >= 0.75:
            colour = _COLOR_HIGH
        else:
            colour = _COLOR_WARN

        symbol_label = event.symbol.upper() if event.symbol else "(unknown)"
        embed = discord.Embed(
            title=f"🚨 Thesis invalidated — {symbol_label}",
            colour=colour,
        )

        embed.add_field(
            name="Symbol",
            value=f"`{symbol_label}`",
            inline=True,
        )
        embed.add_field(
            name="Invalidation score",
            value=f"{score:.0%}",
            inline=True,
        )

        description = (event.trigger_description or "").strip()
        if len(description) > _TRIGGER_DESC_MAX:
            description = description[:_TRIGGER_DESC_MAX] + "…"
        if description:
            embed.add_field(
                name="Trigger",
                value=description,
                inline=False,
            )

        embed.add_field(
            name="Next step",
            value="Review thesis with `/thesis review` or close with `/thesis close`.",
            inline=False,
        )

        embed.set_footer(
            text=f"thesis_id: {event.thesis_id}  •  event_id: {event.event_id}"
        )
        return embed
