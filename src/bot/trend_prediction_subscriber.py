"""TrendPredictionSubscriber — Discord delivery for trend prediction results.

Owner: bot segment.

Consumed event : TrendPredictionCompletedEvent
Side-effect    : Pushes a Discord embed to alert_channel when the trend
                 engine completes a batch scan, surfacing top verdicts
                 so the investor can act before the session opens.

Wiring (app.py on_ready)::

    subscriber = TrendPredictionSubscriber(channel_id=...)
    subscriber.set_client(bot)
    subscriber.register()

This class mirrors the pattern of AgendaSubscriber and
IntelligenceEngineSubscriber — thin bot adapter, no domain logic.
"""
from __future__ import annotations

from typing import Any

import discord

from src.platform.event_bus import get_event_bus
from src.platform.events import TrendPredictionCompletedEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Only push when at least one top verdict exists.
_SKIP_WHEN_EMPTY = True

# Verdict → colour mapping for the embed side-bar.
_VERDICT_COLOURS: dict[str, discord.Colour] = {
    "BULLISH":    discord.Colour.green(),
    "BEARISH":    discord.Colour.red(),
    "WEAKENING":  discord.Colour.orange(),
    "NEUTRAL":    discord.Colour.greyple(),
    "SIDEWAYS":   discord.Colour.greyple(),
}
_DEFAULT_COLOUR = discord.Colour.blurple()


class TrendPredictionSubscriber:
    """Subscribe to TrendPredictionCompletedEvent → push Discord embed.

    Requires .set_client(bot) before first event fires.

    Args:
        channel_id: Discord channel ID for trend pushes.
                    Falls back to settings.alert_channel_id when None.
    """

    def __init__(self, channel_id: int | None = None) -> None:
        self._channel_id = channel_id
        self._client: Any | None = None

    def set_client(self, client: Any) -> None:
        """Inject Discord bot client. Called by app.py after bootstrap."""
        self._client = client
        logger.info("trend_prediction_subscriber.discord_client_injected")

    def register(self) -> None:
        get_event_bus().subscribe_handler(TrendPredictionCompletedEvent, self._handle)
        logger.info("trend_prediction_subscriber.registered")

    def _resolve_channel_id(self) -> int | None:
        if self._channel_id is not None:
            return self._channel_id
        try:
            from src.platform.config import settings
            raw = getattr(settings, "alert_channel_id", None)
            return int(raw) if raw else None
        except Exception:
            return None

    async def _handle(self, event: TrendPredictionCompletedEvent) -> None:
        if _SKIP_WHEN_EMPTY and not event.top_verdicts:
            logger.debug(
                "trend_prediction_subscriber.discord_skip",
                reason="no_top_verdicts",
                phase=event.scan_phase,
            )
            return

        if self._client is None:
            logger.warning(
                "trend_prediction_subscriber.discord_skip",
                reason="no_client_injected",
                phase=event.scan_phase,
            )
            return

        channel_id = self._resolve_channel_id()
        if channel_id is None:
            logger.warning(
                "trend_prediction_subscriber.discord_skip",
                reason="no_channel_id_configured",
                phase=event.scan_phase,
            )
            return

        channel = self._client.get_channel(channel_id)
        if channel is None:
            logger.warning(
                "trend_prediction_subscriber.discord_channel_not_found",
                channel_id=channel_id,
                phase=event.scan_phase,
            )
            return

        try:
            embed = self._build_embed(event)
            await channel.send(embed=embed)
            logger.info(
                "trend_prediction_subscriber.discord_sent",
                phase=event.scan_phase,
                symbols_analyzed=event.symbols_analyzed,
                top_count=len(event.top_verdicts),
                channel_id=channel_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "trend_prediction_subscriber.discord_error",
                error=str(exc),
                phase=event.scan_phase,
            )

    # ── embed builder ──────────────────────────────────────────────────────

    @staticmethod
    def _build_embed(event: TrendPredictionCompletedEvent) -> discord.Embed:
        """Build a compact trend prediction embed.

        Layout:
            Title  : 📈 Trend scan — {phase}
            Fields : top_verdicts as TICKER: VERDICT rows
            Footer : symbols_analyzed total
        """
        # Derive colour from the first (highest-confidence) verdict.
        top_colour = _DEFAULT_COLOUR
        if event.top_verdicts:
            first_verdict = event.top_verdicts[0][1].upper()
            top_colour = _VERDICT_COLOURS.get(first_verdict, _DEFAULT_COLOUR)

        phase_label = event.scan_phase.upper()
        embed = discord.Embed(
            title=f"📈 Trend scan — {phase_label}",
            colour=top_colour,
        )

        if event.top_verdicts:
            lines = [
                f"`{symbol}` — **{verdict}**"
                for symbol, verdict in event.top_verdicts
            ]
            embed.add_field(
                name="Top verdicts",
                value="\n".join(lines),
                inline=False,
            )
        else:
            embed.add_field(
                name="Top verdicts",
                value="Không có tín hiệu nổi bật.",
                inline=False,
            )

        embed.set_footer(
            text=f"Đã phân tích {event.symbols_analyzed} mã · phase {event.scan_phase}"
        )
        return embed
