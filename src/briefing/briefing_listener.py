"""BriefingListener — Event-driven bridge from BriefingRequestedEvent → BriefingService.

Owner: briefing segment.
Subscribes: BriefingRequestedEvent, DailyAgendaCompletedEvent
Emits:      BriefingReadyEvent (consumed by readmodel.CacheSubscriber for cache invalidation)

Boundary:
- Listener nhận event, resolve deps, gọi BriefingService, gửi Discord channel.
- Không chứa logic generate brief — đó là BriefingService / BriefingAgent.
- Discord delivery nằm ở đây thay vì bot/scheduler vì briefing là domain
  concern (ai gửi gì, ở đâu) chứ không phải bot timing concern.
- discord.Client được inject sau khi bot login (set_client) để tránh coupling
  bootstrap với discord runtime.

Wave B activation:
- agenda_service_factory injected at construction time (from bootstrap).
- Passed into BriefingService so morning/eod briefs include today's agenda context
  (decide/watch/defer) built by AgendaBuilderScheduler at 07:30 ICT.
- Fully backward-compatible: agenda_service_factory=None → no agenda context, no error.

P1 (Agenda → Morning Brief):
- Subscribes DailyAgendaCompletedEvent and caches a compact agenda summary per user.
- When generating a brief, if a cached agenda exists for that user, it is
  prepended to the brief embed description so Morning Brief is visibly
  anchored around today's DECIDE/WATCH/DEFER list.
- This is a UI-level join and does NOT change BriefingService or BriefingAgent
  contracts — safe first step to increase perceived cohesion.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

from src.platform.event_bus import get_event_bus
from src.platform.events import (
    BriefingReadyEvent,
    BriefingRequestedEvent,
    DailyAgendaCompletedEvent,
)
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
        agenda_service_factory: object | None = None,
    ) -> None:
        self._client = discord_client
        self._morning_channel_id = morning_channel_id
        self._eod_channel_id = eod_channel_id
        self._user_id = user_id
        # Wave B: callable(session) -> AgendaService | None
        # Injected from bootstrap so BriefingService can include agenda context.
        self._agenda_service_factory = agenda_service_factory
        # P1: in-memory cache of last DailyAgendaCompletedEvent per user_id → compact summary string.
        # Single-user app today, but keyed by user_id for future multi-user safety.
        self._agenda_cache: dict[str, str] = {}

    def set_client(self, client: "discord.Client") -> None:
        """Inject discord.Client after bot login (called from bot on_ready)."""
        self._client = client
        logger.info("briefing_listener.client_injected")

    def register(self) -> None:
        """Subscribe BriefingRequestedEvent on the global event bus."""
        bus = get_event_bus()
        bus.subscribe_handler(BriefingRequestedEvent, self._handle)
        # P1: also listen to DailyAgendaCompletedEvent so Morning Brief can be anchored to agenda.
        bus.subscribe_handler(DailyAgendaCompletedEvent, self._handle_agenda)
        logger.info(
            "briefing_listener.registered",
            agenda_service_wired=self._agenda_service_factory is not None,
        )

    async def _handle_agenda(self, event: DailyAgendaCompletedEvent) -> None:
        """Cache a compact agenda summary string for the given user.

        Uses only the event payload (no DB/AI calls) so this handler is cheap
        and safe to run before Morning Brief. The cached block is later
        prepended to the brief embed description for visual cohesion.
        """
        try:
            # Basic guard: if there is literally nothing, clear cache and return.
            if (
                event.decide_count <= 0
                and event.watch_count <= 0
                and event.defer_count <= 0
            ):
                self._agenda_cache.pop(event.user_id, None)
                logger.info(
                    "briefing_listener.agenda_cleared",
                    user_id=event.user_id,
                )
                return

            lines: list[str] = ["Daily Agenda:"]
            if event.decide_tickers:
                decide_line = f"DECIDE ({event.decide_count}): {', '.join(event.decide_tickers)}"
                lines.append(decide_line)
            elif event.decide_count:
                lines.append(f"DECIDE ({event.decide_count}): ...")

            if event.watch_tickers:
                watch_line = f"WATCH ({event.watch_count}): {', '.join(event.watch_tickers)}"
                lines.append(watch_line)
            elif event.watch_count:
                lines.append(f"WATCH ({event.watch_count}): ...")

            if event.defer_count:
                # DailyAgendaCompletedEvent does not carry defer tickers yet —
                # we surface only the count to avoid schema churn in P1.
                lines.append(f"DEFER ({event.defer_count}): {event.defer_count} tickers")

            if event.opening_line:
                lines.append(f"Summary: {event.opening_line}")

            summary = "\n".join(lines)
            self._agenda_cache[event.user_id] = summary

            logger.info(
                "briefing_listener.agenda_cached",
                user_id=event.user_id,
                decide_count=event.decide_count,
                watch_count=event.watch_count,
                defer_count=event.defer_count,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "briefing_listener.agenda_cache_failed",
                user_id=event.user_id,
                error=str(exc),
            )

    async def _handle(self, event: BriefingRequestedEvent) -> None:
        from src.bot.commands.briefing import build_brief_embed
        from src.briefing.service import BriefingService
        from src.platform.bootstrap import (
            get_briefing_agent,
            get_pnl_service,
            get_quote_service,
            get_sector_rotation_agent,
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
                # Wave B: build AgendaService instance for this session if factory is wired.
                agenda_svc = (
                    self._agenda_service_factory(session)  # type: ignore[operator]
                    if self._agenda_service_factory is not None
                    else None
                )

                svc = BriefingService(
                    watchlist_service=WatchlistService(session=session),
                    quote_service=get_quote_service(),
                    briefing_agent=get_briefing_agent(),
                    pnl_service=get_pnl_service()(session),
                    session=session,
                    sector_rotation_agent=get_sector_rotation_agent(),
                    agenda_service=agenda_svc,  # Wave B: inject agenda context (fallback path)
                )
                if phase == "morning":
                    brief_result = await svc.generate_morning_brief(user_id=self._user_id)
                else:
                    brief_result = await svc.generate_eod_brief(user_id=self._user_id)
                await session.commit()

            logger.info(
                "briefing_listener.brief_generated",
                phase=phase,
                has_agenda=agenda_svc is not None,
            )

            embed = build_brief_embed(brief_result.output, phase=phase)

            # P1: prepend cached agenda summary to embed description when available.
            agenda_block = self._agenda_cache.get(self._user_id)
            if agenda_block:
                original_desc = embed.description or ""
                # Avoid duplicate blank lines when original_desc is empty.
                if original_desc:
                    embed.description = f"{agenda_block}\n\n{original_desc}"
                else:
                    embed.description = agenda_block

            await channel.send(embed=embed)  # type: ignore[union-attr]
            logger.info(
                "briefing_listener.sent",
                phase=phase,
                channel_id=channel_id,
                event_id=event.event_id,
                triggered_by=event.triggered_by,
            )

            # Emit BriefingReadyEvent — consumed by readmodel.CacheSubscriber
            # to invalidate brief_latest cache for this user.
            # Wave 3: pass user_id so cache invalidation is per-user.
            bus = get_event_bus()
            await bus.publish(
                BriefingReadyEvent(
                    brief_type=phase,
                    channel="discord",
                    content_summary=getattr(brief_result.output, "summary", "")[:200],
                    user_id=self._user_id,
                )
            )

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "briefing_listener.error",
                phase=phase,
                event_id=event.event_id,
                triggered_by=event.triggered_by,
                error=str(exc),
            )
            raise  # Re-raise → EventBus records in dead_letters, worker continues
