"""
Evolution subscriber — bot segment.

Subscribes EvolutionSuggestionReadyEvent and pushes a Discord embed
to the owner for review. Never auto-applies any suggestion.

Owner: bot segment (thin adapter).
"""
from __future__ import annotations

import discord

from src.platform.event_bus import get_event_bus
from src.platform.events import EvolutionSuggestionReadyEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Accuracy thresholds for embed colour
_COLOR_GOOD    = 0x2ECC71  # green  — accuracy >= 70%
_COLOR_CAUTION = 0xE67E22  # orange — accuracy >= 50%
_COLOR_WEAK    = 0xE74C3C  # red    — accuracy < 50%
_COLOR_UNKNOWN = 0x95A5A6  # grey   — accuracy not available


class EvolutionSubscriber:
    """Receives EvolutionSuggestionReadyEvent → Discord embed for owner review.

    Embed structure:
        Title   : 🧬 Evolution scan — N suggestions
        Colour  : green / orange / red based on overall_accuracy
        Fields  : suggestion_count, period_days, has_high_risk
        Footer  : reminder that all suggestions require human approval
    """

    def __init__(self, channel_id: int) -> None:
        self._channel_id = channel_id
        self._client: discord.Client | None = None

    def set_client(self, client: discord.Client) -> None:
        self._client = client

    def register(self) -> None:
        bus = get_event_bus()
        bus.subscribe(EvolutionSuggestionReadyEvent, self._handle)
        logger.info("evolution_subscriber.registered")

    async def _handle(self, event: EvolutionSuggestionReadyEvent) -> None:
        if event.suggestion_count == 0:
            logger.info("evolution_subscriber.skip", reason="no_suggestions")
            return

        if self._client is None:
            logger.warning("evolution_subscriber.no_client")
            return

        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            logger.warning("evolution_subscriber.channel_not_found", channel_id=self._channel_id)
            return

        embed = self._build_embed(event)
        try:
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "evolution_subscriber.sent",
                suggestion_count=event.suggestion_count,
                channel_id=self._channel_id,
            )
        except Exception as exc:
            logger.exception("evolution_subscriber.send_failed", error=str(exc))

    def _build_embed(self, event: EvolutionSuggestionReadyEvent) -> discord.Embed:
        # Colour based on accuracy
        if event.overall_accuracy <= 0.0:
            colour = _COLOR_UNKNOWN
            accuracy_label = "n/a"
        elif event.overall_accuracy >= 0.70:
            colour = _COLOR_GOOD
            accuracy_label = f"{event.overall_accuracy:.0%}"
        elif event.overall_accuracy >= 0.50:
            colour = _COLOR_CAUTION
            accuracy_label = f"{event.overall_accuracy:.0%}"
        else:
            colour = _COLOR_WEAK
            accuracy_label = f"{event.overall_accuracy:.0%}"

        embed = discord.Embed(
            title=f"🧬 Evolution scan — {event.suggestion_count} suggestion(s)",
            colour=colour,
        )

        embed.add_field(
            name="Suggestions",
            value=str(event.suggestion_count),
            inline=True,
        )
        embed.add_field(
            name="Period",
            value=f"{event.period_days}d",
            inline=True,
        )
        embed.add_field(
            name="Accuracy",
            value=accuracy_label,
            inline=True,
        )

        risk_label = "⚠️ Yes — review carefully" if event.has_high_risk else "Low"
        embed.add_field(
            name="High-risk item?",
            value=risk_label,
            inline=False,
        )

        embed.add_field(
            name="Next step",
            value="Run `/evolution list` to review pending suggestions.\nAll changes require manual approval — nothing is auto-applied.",
            inline=False,
        )

        embed.set_footer(text=f"run_id: {event.run_id}  •  requires_human_approval: always")
        return embed
