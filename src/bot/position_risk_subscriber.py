"""
PositionRiskSubscriber — bot segment.

Subscribes PositionRiskBreachedEvent and pushes a Discord embed
so the owner is alerted proactively when a position breaches a
loss threshold — without needing to run /portfolio manually.

Owner: bot segment (thin adapter — no domain logic).
Emitter: portfolio/pnl_service.py._maybe_emit_risk_breach()

Thresholds (set in pnl_service):
  CRITICAL: unrealized_pct <= -15%  → red embed, urgency CRITICAL
  WARN:     unrealized_pct <= -8%   → orange embed, urgency TODAY

Dedup: upstream publishes with
  dedup_key=f"position_risk:{user_id}:{symbol}:{breach_type}"
  dedup_window=6h — no double-alert within 6 hours per position.
"""
from __future__ import annotations

import discord

from src.platform.event_bus import get_event_bus
from src.platform.events import PositionRiskBreachedEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)

_COLOR_CRITICAL = 0xE74C3C   # red
_COLOR_WARN     = 0xE67E22   # orange

_URGENCY_EMOJI = {
    "CRITICAL": "🔴",
    "TODAY":    "🟠",
}


class PositionRiskSubscriber:
    """Receives PositionRiskBreachedEvent → Discord embed for owner.

    Embed structure:
        Title   : {emoji} Position risk — {SYMBOL}: {BREACH_TYPE}
        Colour  : red (CRITICAL) / orange (TODAY)
        Fields  : symbol, urgency, current loss %, threshold %,
                  current_value vs threshold_value,
                  next step action
        Footer  : breach_type · event_id
    """

    def __init__(self, channel_id: int) -> None:
        self._channel_id = channel_id
        self._client: discord.Client | None = None

    def set_client(self, client: discord.Client) -> None:
        self._client = client

    def register(self) -> None:
        bus = get_event_bus()
        bus.subscribe(PositionRiskBreachedEvent, self._handle)
        logger.info("position_risk_subscriber.registered", channel_id=self._channel_id)

    async def _handle(self, event: PositionRiskBreachedEvent) -> None:
        if self._client is None:
            logger.warning("position_risk_subscriber.no_client")
            return

        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            logger.warning(
                "position_risk_subscriber.channel_not_found",
                channel_id=self._channel_id,
            )
            return

        embed = self._build_embed(event)
        try:
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "position_risk_subscriber.sent",
                symbol=event.symbol,
                breach_type=event.breach_type,
                urgency=event.urgency,
                current_value=event.current_value,
            )
        except Exception as exc:
            logger.exception("position_risk_subscriber.send_failed", error=str(exc))

    def _build_embed(self, event: PositionRiskBreachedEvent) -> discord.Embed:
        urgency = (event.urgency or "TODAY").upper()
        symbol  = (event.symbol or "?").upper()
        breach  = event.breach_type or "LOSS_PCT"

        colour = _COLOR_CRITICAL if urgency == "CRITICAL" else _COLOR_WARN
        emoji  = _URGENCY_EMOJI.get(urgency, "⚪")

        embed = discord.Embed(
            title=f"{emoji} Position risk — {symbol}",
            colour=colour,
        )

        # Loss vs threshold
        embed.add_field(
            name="Unrealized loss",
            value=f"`{event.current_value:.1f}%`",
            inline=True,
        )
        embed.add_field(
            name="Threshold",
            value=f"`{event.threshold_value:.0f}%`",
            inline=True,
        )
        embed.add_field(
            name="Urgency",
            value=f"`{urgency}`",
            inline=True,
        )

        # Next step
        if urgency == "CRITICAL":
            action = (
                f"`{symbol}` đang lỗ **{abs(event.current_value):.1f}%** — vượt ngưỡng tối. "
                "Kiểm tra stop-loss ngay: `/portfolio` hoặc review thesis gốc."
            )
        else:
            action = (
                f"`{symbol}` đang lỗ **{abs(event.current_value):.1f}%**. "
                "Xem xét review thesis hoặc điều chỉnh position size."
            )

        embed.add_field(name="Next step", value=action, inline=False)

        embed.set_footer(
            text=f"{breach}  •  event_id: {event.event_id}"
        )
        return embed
