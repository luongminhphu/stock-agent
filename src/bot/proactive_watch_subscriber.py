"""ProactiveWatchSubscriber — Discord delivery adapter for proactive watch alerts.

Owner: bot segment (thin adapter — no business logic).

Responsibilities:
  - Subscribe to ProactiveWatchAlertFiredEvent (emitted by watchlist segment)
  - Batch events that arrive in the same scan phase within a short window
  - Build and send a Discord embed to alert_channel_id

Batching strategy:
    Events from the same scan cycle share the same scan_event_id.
    We accumulate events for up to _BATCH_WINDOW_SECONDS (2 s) then flush.
    If only 1 event fires → single detailed embed.
    If > 1 → batch summary embed.

Does NOT contain watchlist/scan logic — that is ProactiveWatchListener.
"""

from __future__ import annotations

import asyncio
import datetime

import discord

from src.bot.commands.proactive_watch_embeds import (
    build_proactive_watch_embed,
    build_proactive_watch_batch_embed,
)
from src.platform.event_bus import get_event_bus
from src.platform.events import ProactiveWatchAlertFiredEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)

_BATCH_WINDOW_SECONDS = 2


class ProactiveWatchSubscriber:
    """Receive ProactiveWatchAlertFiredEvent → send Discord embed."""

    def __init__(self, channel_id: int | None = None) -> None:
        self._channel_id = channel_id
        self._client: discord.Client | None = None
        self._pending: dict[str, tuple[asyncio.Task, list[ProactiveWatchAlertFiredEvent]]] = {}

    def set_client(self, client: discord.Client) -> None:
        self._client = client

    def set_channel_id(self, channel_id: int) -> None:
        self._channel_id = channel_id

    def register(self) -> None:
        bus = get_event_bus()
        bus.subscribe_handler(ProactiveWatchAlertFiredEvent, self._handle)
        logger.info("proactive_watch_subscriber.registered", channel_id=self._channel_id)

    async def _handle(self, event: ProactiveWatchAlertFiredEvent) -> None:
        scan_id = event.scan_event_id or event.event_id

        if scan_id in self._pending:
            task, events = self._pending[scan_id]
            events.append(event)
        else:
            events: list[ProactiveWatchAlertFiredEvent] = [event]  # type: ignore[no-redef]
            task = asyncio.get_running_loop().create_task(
                self._flush_after_window(scan_id, events)
            )
            self._pending[scan_id] = (task, events)

    async def _flush_after_window(
        self,
        scan_id: str,
        events: list[ProactiveWatchAlertFiredEvent],
    ) -> None:
        await asyncio.sleep(_BATCH_WINDOW_SECONDS)
        self._pending.pop(scan_id, None)

        if not events:
            return

        if not self._client or not self._channel_id:
            logger.warning(
                "proactive_watch_subscriber.no_client_or_channel",
                fired_count=len(events),
                tickers=[e.ticker for e in events],
            )
            return

        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            logger.warning(
                "proactive_watch_subscriber.channel_not_found",
                channel_id=self._channel_id,
            )
            return

        now_utc = datetime.datetime.now(tz=datetime.UTC)
        try:
            if len(events) == 1:
                e = events[0]
                embed = build_proactive_watch_embed(
                    ticker=e.ticker,
                    condition=e.condition,
                    priority=e.priority,
                    details=e.details,
                    triggered_at=e.triggered_at,
                )
            else:
                embed = build_proactive_watch_batch_embed(events, now_utc)

            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "proactive_watch_subscriber.sent",
                fired_count=len(events),
                tickers=sorted({e.ticker for e in events}),
                phase=events[0].phase,
                channel_id=self._channel_id,
            )
        except Exception as exc:
            logger.error(
                "proactive_watch_subscriber.send_error",
                error=str(exc),
                fired_count=len(events),
            )
