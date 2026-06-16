"""
StressTestSubscriber — bot segment.

Subscribes StressTestCompletedEvent and pushes a Discord embed
so the owner receives stress-test results proactively, not just
when they explicitly requested via slash command.

Owner: bot segment (thin adapter — no domain logic).
Emitter: thesis/stress_test_service.py

Dedup: upstream publishes with dedup_key=f"stress_test:{thesis_id}",
dedup_window=2h — no double-alert within 2 hours for the same thesis.

Verdict colour mapping:
  FAIL / invalidation_probability >= 0.70 → red    (act now)
  WARN / invalidation_probability >= 0.40 → orange (watch closely)
  PASS / otherwise                         → green  (thesis holding)
"""
from __future__ import annotations

import discord

from src.platform.event_bus import get_event_bus
from src.platform.events import StressTestCompletedEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)

_COLOR_FAIL = 0xE74C3C   # red
_COLOR_WARN = 0xE67E22   # orange
_COLOR_PASS = 0x27AE60   # green

_SCENARIO_MAX    = 300
_TRIGGERS_SHOWN  = 3

_VERDICT_EMOJI = {
    "FAIL":    "🔴",
    "WARN":    "🟠",
    "PASS":    "🟢",
    "UNKNOWN": "⚪",
}


class StressTestSubscriber:
    """Receives StressTestCompletedEvent → Discord embed for owner.

    Embed structure:
        Title   : {emoji} Stress test — {SYMBOL}: {VERDICT}
        Colour  : red / orange / green
        Fields  : symbol, verdict, invalidation probability,
                  broken + weakened assumption counts,
                  stress scenario (truncated),
                  top suggested triggers to watch,
                  next step action
        Footer  : thesis_id + event_id
    """

    def __init__(self, channel_id: int) -> None:
        self._channel_id = channel_id
        self._client: discord.Client | None = None

    def set_client(self, client: discord.Client) -> None:
        self._client = client

    def register(self) -> None:
        bus = get_event_bus()
        bus.subscribe_handler(StressTestCompletedEvent, self._handle)
        logger.info("stress_test_subscriber.registered", channel_id=self._channel_id)

    async def _handle(self, event: StressTestCompletedEvent) -> None:
        if self._client is None:
            logger.warning("stress_test_subscriber.no_client")
            return

        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            logger.warning(
                "stress_test_subscriber.channel_not_found",
                channel_id=self._channel_id,
            )
            return

        embed = self._build_embed(event)
        try:
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "stress_test_subscriber.sent",
                symbol=event.symbol,
                verdict=event.verdict,
                invalidation_probability=event.invalidation_probability,
                thesis_id=event.thesis_id,
            )
        except Exception as exc:
            logger.exception("stress_test_subscriber.send_failed", error=str(exc))

    def _build_embed(self, event: StressTestCompletedEvent) -> discord.Embed:
        verdict_upper = (event.verdict or "UNKNOWN").upper()
        prob = event.invalidation_probability

        # Colour: verdict label takes priority, fallback to probability threshold
        if verdict_upper == "FAIL" or prob >= 0.70:
            colour = _COLOR_FAIL
            verdict_key = "FAIL"
        elif verdict_upper == "WARN" or prob >= 0.40:
            colour = _COLOR_WARN
            verdict_key = "WARN"
        elif verdict_upper == "PASS":
            colour = _COLOR_PASS
            verdict_key = "PASS"
        else:
            colour = _COLOR_PASS
            verdict_key = "UNKNOWN"

        emoji  = _VERDICT_EMOJI.get(verdict_key, "⚪")
        symbol = (event.symbol or "?").upper()
        title_text = event.thesis_title or symbol
        if len(title_text) > 60:
            title_text = title_text[:57] + "..."

        embed = discord.Embed(
            title=f"{emoji} Stress test — {symbol}: {verdict_upper}",
            description=f"*{title_text}*",
            colour=colour,
        )

        # Invalidation probability + confidence
        embed.add_field(
            name="Invalidation probability",
            value=f"`{prob:.0%}`",
            inline=True,
        )
        embed.add_field(
            name="Độ tin cậy",
            value=f"`{event.confidence:.0%}`",
            inline=True,
        )

        # Assumption damage summary
        broken   = event.broken_assumption_count
        weakened = event.weakened_assumption_count
        if broken or weakened:
            damage_parts = []
            if broken:   damage_parts.append(f"🔴 {broken} broken")
            if weakened: damage_parts.append(f"🟠 {weakened} weakened")
            embed.add_field(
                name="Assumptions",
                value=" · ".join(damage_parts),
                inline=False,
            )

        # Stress scenario (truncated)
        scenario = (event.stress_scenario or "").strip()
        if len(scenario) > _SCENARIO_MAX:
            scenario = scenario[:_SCENARIO_MAX] + "…"
        if scenario:
            embed.add_field(
                name="Stress scenario",
                value=scenario,
                inline=False,
            )

        # Top suggested triggers
        triggers = list(event.suggested_triggers or [])
        if triggers:
            top = triggers[:_TRIGGERS_SHOWN]
            trigger_lines = "\n".join(f"• {t}" for t in top)
            if len(triggers) > _TRIGGERS_SHOWN:
                trigger_lines += f"\n… và {len(triggers) - _TRIGGERS_SHOWN} trigger khác"
            embed.add_field(
                name="Triggers to watch",
                value=trigger_lines,
                inline=False,
            )

        # Next step action
        if verdict_key == "FAIL":
            action = (
                f"Thesis `{symbol}` đang chịu áp lực cao. "
                "Review ngay với `/thesis review` hoặc cân nhắc `/thesis close`."
            )
        elif verdict_key == "WARN":
            action = (
                f"Theo dõi sát `{symbol}`. "
                "Chạy `/thesis review` để kiểm tra conviction hiện tại."
            )
        else:
            action = f"Thesis `{symbol}` vẫn holding. Theo dõi các trigger trên."

        embed.add_field(name="Next step", value=action, inline=False)

        embed.set_footer(
            text=f"thesis_id: {event.thesis_id}  •  event_id: {event.event_id}"
        )
        return embed
