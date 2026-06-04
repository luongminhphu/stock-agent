"""
TrendShiftSubscriber — bot segment.

Subscribes TrendShiftEvent and pushes a Discord embed
so the owner is alerted when a portfolio symbol changes market regime.

Owner: bot segment (thin adapter — no domain logic).
Emitter: market/trend_shift_detector.py

Severity:
  MAJOR — regime polarity flipped AND composite crossed zone boundary
           (e.g. TRENDING_UP → TRENDING_DOWN with composite 0.7 → 0.3)
  MINOR — regime changed OR composite moved >0.15, still outside neutral band

Noise already filtered by detector:
  - Cold start skipped
  - Neutral band (0.4–0.6) suppresses MINOR
  - Dedup NOT applied here — detector does not dedup; each scan phase
    (morning/midday/pre_atc) is a distinct data point. Owner may want
    to see the same symbol shift across phases.
"""
from __future__ import annotations

import discord

from src.platform.event_bus import get_event_bus
from src.platform.events import TrendShiftEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)

_COLOR_MAJOR = 0xE74C3C   # red    — MAJOR shift, act now
_COLOR_MINOR = 0xF39C12   # amber  — MINOR shift, watch closely

_REGIME_EMOJI = {
    "TRENDING_UP":   "↑",
    "TRENDING_DOWN": "↓",
    "RANGING":       "↔",
    "VOLATILE":      "⚠️",
}

_PHASE_LABEL = {
    "morning":  "Morning (09:05)",
    "midday":   "Midday (11:00)",
    "pre_atc":  "Pre-ATC (14:10)",
}


class TrendShiftSubscriber:
    """Receives TrendShiftEvent → Discord embed for owner.

    Embed structure:
        MAJOR  🚨 Regime shift — {SYMBOL}   (red)
        MINOR  📊 Trend shift — {SYMBOL}    (amber)
        Fields : symbol, severity, regime change, composite delta, scan phase
        Footer : event_id
    """

    def __init__(self, channel_id: int) -> None:
        self._channel_id = channel_id
        self._client: discord.Client | None = None

    def set_client(self, client: discord.Client) -> None:
        self._client = client

    def register(self) -> None:
        bus = get_event_bus()
        bus.subscribe(TrendShiftEvent, self._handle)
        logger.info("trend_shift_subscriber.registered", channel_id=self._channel_id)

    async def _handle(self, event: TrendShiftEvent) -> None:
        if self._client is None:
            logger.warning("trend_shift_subscriber.no_client")
            return

        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            logger.warning(
                "trend_shift_subscriber.channel_not_found",
                channel_id=self._channel_id,
            )
            return

        embed = self._build_embed(event)
        try:
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "trend_shift_subscriber.sent",
                symbol=event.symbol,
                severity=event.shift_severity,
                scan_phase=event.scan_phase,
            )
        except Exception as exc:
            logger.exception("trend_shift_subscriber.send_failed", error=str(exc))

    def _build_embed(self, event: TrendShiftEvent) -> discord.Embed:
        is_major = event.shift_severity == "MAJOR"
        colour = _COLOR_MAJOR if is_major else _COLOR_MINOR
        icon   = "🚨" if is_major else "📊"
        symbol = event.symbol.upper()

        embed = discord.Embed(
            title=f"{icon} {'Regime' if is_major else 'Trend'} shift — {symbol}",
            colour=colour,
        )

        # Regime arrow
        prev_e = _REGIME_EMOJI.get(event.previous_regime, "")
        curr_e = _REGIME_EMOJI.get(event.current_regime,  "")
        embed.add_field(
            name="Regime",
            value=f"{prev_e} `{event.previous_regime}` → {curr_e} `{event.current_regime}`",
            inline=False,
        )

        # Composite delta
        delta = event.composite_delta
        delta_str = f"{'+' if delta >= 0 else ''}{delta:+.2f}"
        embed.add_field(
            name="Composite",
            value=(
                f"`{event.previous_composite:.2f}` → `{event.current_composite:.2f}` "
                f"({delta_str})"
            ),
            inline=True,
        )

        # Severity badge
        embed.add_field(
            name="Severity",
            value=f"`{event.shift_severity}`",
            inline=True,
        )

        # Scan phase
        phase_label = _PHASE_LABEL.get(event.scan_phase or "", event.scan_phase or "unknown")
        embed.add_field(
            name="Scan phase",
            value=phase_label,
            inline=True,
        )

        # Action nudge
        if is_major:
            action = (
                f"Review thesis for `{symbol}` with `/thesis review` — "
                "consider updating conviction or exiting position."
            )
        else:
            action = (
                f"Watch `{symbol}` closely. "
                "Run `/market context {symbol}` for detailed technical context."
            )
        embed.add_field(name="Next step", value=action, inline=False)

        embed.set_footer(text=f"event_id: {event.event_id}")
        return embed
