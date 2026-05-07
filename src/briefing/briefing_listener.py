"""BriefingListener — Event-driven bridge from BriefingRequestedEvent → BriefingService.

Owner: briefing segment.
Subscribes: BriefingRequestedEvent
Emits:      BriefingReadyEvent (for future analytics / readmodel consumers)

Boundary:
- Listener nhận event, resolve deps, gọi BriefingService, gửi Discord channel.
- Không chứa logic generate brief — đó là BriefingService / BriefingAgent.
- Discord delivery nằm ở đây thay vì bot/scheduler vì briefing là domain
  concern (ai gửi gì, ở đâu) chứ không phải bot timing concern.
- discord.Client được inject sau khi bot login (set_client) để tránh coupling
  bootstrap với discord runtime.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

from src.platform.event_bus import get_event_bus
from src.platform.events import BriefingReadyEvent, BriefingRequestedEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)


class BriefingListener:
    """Subscribe BriefingRequestedEvent và execute briefing pipeline."""

    def __init__(
        self,
        morning_channel_id: int | None,
        eod_channel_id: int | None,
        user_id: str,
        discord_client: "discord.Client | None" = None,
    ) -> None:
        self._client = discord_client
        self._morning_channel_id = morning_channel_id
        self._eod_channel_id = eod_channel_id
        self._user_id = user_id

    def set_client(self, client: "discord.Client") -> None:
        """Inject discord.Client after bot login (called from bot on_ready)."""
        self._client = client
        logger.info("briefing_listener.client_injected")

    def register(self) -> None:
        """Subscribe BriefingRequestedEvent on the global event bus."""
        bus = get_event_bus()
        bus.subscribe_handler(BriefingRequestedEvent, self._handle)
        logger.info("briefing_listener.registered")

    async def _handle(self, event: BriefingRequestedEvent) -> None:
        from src.bot.commands.briefing import build_brief_embed
        from src.briefing.service import BriefingService
        from src.platform.bootstrap import (
            get_briefing_agent,
            get_pnl_service,
            get_quote_service,
        )
        from src.platform.db import AsyncSessionLocal
        from src.watchlist.service import WatchlistService

        phase = event.brief_type  # "morning" | "eod"

        if self._client is None:
            logger.warning(
                "briefing_listener.no_client",
                phase=phase,
                reason="discord_client not injected yet — call set_client() in on_ready",
            )
            return

        channel_id = (
            self._morning_channel_id if phase == "morning" else self._eod_channel_id
        )
        if not channel_id:
            logger.warning(
                "briefing_listener.no_channel",
                phase=phase,
                reason="channel_id not configured",
            )
            return

        channel = self._client.get_channel(channel_id)
        if channel is None:
            logger.warning(
                "briefing_listener.channel_not_found",
                channel_id=channel_id,
                phase=phase,
            )
            return

        try:
            async with AsyncSessionLocal() as session:
                svc = BriefingService(
                    watchlist_service=WatchlistService(session=session),
                    quote_service=get_quote_service(),
                    briefing_agent=get_briefing_agent(),
                    pnl_service=get_pnl_service()(session),
                    session=session,
                )
                if phase == "morning":
                    brief = await svc.generate_morning_brief(user_id=self._user_id)
                else:
                    brief = await svc.generate_eod_brief(user_id=self._user_id)
                await session.commit()

            embed = build_brief_embed(brief, phase=phase)
            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "briefing_listener.sent",
                phase=phase,
                channel_id=channel_id,
                event_id=event.event_id,
                triggered_by=event.triggered_by,
            )

            # Emit BriefingReadyEvent for future consumers (analytics, readmodel)
            bus = get_event_bus()
            await bus.publish(
                BriefingReadyEvent(
                    brief_type=phase,
                    channel="discord",
                    content_summary=getattr(brief, "summary", "")[:200],
                )
            )

        except Exception as exc:
            logger.exception(
                "briefing_listener.error",
                phase=phase,
                event_id=event.event_id,
                triggered_by=event.triggered_by,
                error=str(exc),
            )
            raise  # Re-raise → EventBus records in dead_letters, worker continues
