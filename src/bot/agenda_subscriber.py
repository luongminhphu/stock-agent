"""AgendaSubscriber — Discord delivery for daily agenda.

Owner: bot segment.

Consumed event : DailyAgendaCompletedEvent
Side-effect    : Pushes a Discord embed to morning_channel at 07:30 ICT
                 summarising today's decide / watch / defer lists.

Wiring (app.py on_ready):
    subscriber = AgendaSubscriber(channel_id=...)
    subscriber.set_client(bot)
    subscriber.register()

This class mirrors the pattern of IntelligenceEngineSubscriber and
PostMortemSubscriber — thin bot adapter, no domain logic.
"""
from __future__ import annotations

from typing import Any

import discord

from src.platform.event_bus import get_event_bus
from src.platform.events import DailyAgendaCompletedEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)


class AgendaSubscriber:
    """Subscribe to DailyAgendaCompletedEvent → push Discord embed.

    Requires .set_client(bot) before first event fires.

    Args:
        channel_id: Discord channel ID for agenda pushes.
                    Falls back to settings.morning_channel_id when None.
    """

    def __init__(self, channel_id: int | None = None) -> None:
        self._channel_id = channel_id
        self._client: Any | None = None

    def set_client(self, client: Any) -> None:
        """Inject Discord bot client. Called by app.py after bootstrap."""
        self._client = client
        logger.info("agenda_subscriber.discord_client_injected")

    def register(self) -> None:
        get_event_bus().subscribe(DailyAgendaCompletedEvent, self._handle)
        logger.info("agenda_subscriber.registered")

    def _resolve_channel_id(self) -> int | None:
        if self._channel_id is not None:
            return self._channel_id
        try:
            from src.platform.config import settings
            raw = (
                getattr(settings, "morning_channel_id", None)
                or getattr(settings, "alert_channel_id", None)
            )
            return int(raw) if raw else None
        except Exception:
            return None

    async def _handle(self, event: DailyAgendaCompletedEvent) -> None:
        if self._client is None:
            logger.warning(
                "agenda_subscriber.discord_skip",
                reason="no_client_injected",
                user_id=event.user_id,
            )
            return

        channel_id = self._resolve_channel_id()
        if channel_id is None:
            logger.warning(
                "agenda_subscriber.discord_skip",
                reason="no_channel_id_configured",
                user_id=event.user_id,
            )
            return

        channel = self._client.get_channel(channel_id)
        if channel is None:
            logger.warning(
                "agenda_subscriber.discord_channel_not_found",
                channel_id=channel_id,
                user_id=event.user_id,
            )
            return

        try:
            embed = self._build_embed(event)
            await channel.send(embed=embed)
            logger.info(
                "agenda_subscriber.discord_sent",
                user_id=event.user_id,
                decide_count=event.decide_count,
                watch_count=event.watch_count,
                channel_id=channel_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "agenda_subscriber.discord_error",
                error=str(exc),
                user_id=event.user_id,
            )

    # ── embed builder ─────────────────────────────────────────────────────

    @staticmethod
    def _build_embed(event: DailyAgendaCompletedEvent) -> discord.Embed:
        """Build a compact daily agenda embed.

        Layout:
            Title  : 📋 Agenda hôm nay
            Desc   : opening_line (AI-generated 1-sentence summary)
            Fields : ✅ Quyết định | 👀 Theo dõi | ⏸ Defer
            Footer : decide_count + watch_count + defer_count totals
        """
        # Colour: green when decide > 0, yellow when watch-only, grey otherwise
        if event.decide_count > 0:
            colour = discord.Colour.green()
        elif event.watch_count > 0:
            colour = discord.Colour.gold()
        else:
            colour = discord.Colour.greyple()

        embed = discord.Embed(
            title="📋 Agenda hôm nay",
            description=event.opening_line or "Không có tóm tắt.",
            colour=colour,
        )

        if event.decide_tickers:
            embed.add_field(
                name=f"✅ Quyết định ({event.decide_count})",
                value="  ".join(f"`{t}`" for t in event.decide_tickers) or "—",
                inline=False,
            )

        if event.watch_tickers:
            embed.add_field(
                name=f"👀 Theo dõi ({event.watch_count})",
                value="  ".join(f"`{t}`" for t in event.watch_tickers) or "—",
                inline=False,
            )

        if event.defer_count > 0:
            embed.add_field(
                name=f"⏸ Defer ({event.defer_count})",
                value="Không cần hành động hôm nay.",
                inline=False,
            )

        embed.set_footer(
            text=(
                f"decide {event.decide_count} · "
                f"watch {event.watch_count} · "
                f"defer {event.defer_count}"
            )
        )
        return embed
