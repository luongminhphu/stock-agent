"""BriefingListener — Event-driven bridge from BriefingRequestedEvent → BriefingService.

Owner: briefing segment.
Subscribes: BriefingRequestedEvent, DailyAgendaCompletedEvent
Emits:      BriefingReadyEvent (consumed by readmodel.CacheSubscriber for cache invalidation)

Boundary (boundary fix B5):
- Listener nhận event, resolve deps, gọi BriefingService, rồi giao kết quả cho
  ``BriefDelivery`` (protocol dưới đây). briefing quyết định *gửi gì*; adapter
  (``bot.brief_delivery.DiscordBriefDelivery``) quyết định *gửi ở đâu, dạng gì*.
- Không chứa logic generate brief — đó là BriefingService / BriefingAgent.
- Không import discord hay src.bot — import-linter contract domain-no-adapters.

Wave B activation:
- agenda_service_factory injected at construction time (from bootstrap).
- Passed into BriefingService so morning/eod briefs include today's agenda context
  (decide/watch/defer) built by AgendaBuilderScheduler at 07:30 ICT.
- Fully backward-compatible: agenda_service_factory=None → no agenda context, no error.

P1 (Agenda → Morning Brief):
- Subscribes DailyAgendaCompletedEvent và cache một agenda summary compact per user.
- Khi generate brief, nếu có cached agenda cho user đó, block này được
  prepend vào embed description để Morning Brief bám sát DECIDE/WATCH/DEFER.

P1.5 (Unify scheduler + slash command experience):
- Cache được đưa ra shared module agenda_cache để BriefingCog có thể
  đọc cùng một Daily Agenda block cho /morning_brief và /eod_brief.
- Đây vẫn là join ở tầng trình bày; contract của BriefingService và
  BriefingAgent không thay đổi.

Wave B.1 (AgendaBuckets → BriefingService):
- DailyAgendaCompletedEvent được map thành AgendaBuckets(decide/watch/defer)
  và lưu kèm summary trong CachedAgenda.
- BriefingService có thể đọc lại buckets để enforce mapping DECIDE → ACT_TODAY
  ở tầng domain-level, độc lập với LLM prompt.
"""

from __future__ import annotations

from typing import Any, Protocol

from src.briefing.agenda_cache import AgendaBuckets, get_agenda, set_agenda
from src.platform.event_bus import get_event_bus
from src.platform.events import (
    BriefingReadyEvent,
    BriefingRequestedEvent,
    DailyAgendaCompletedEvent,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)


class BriefDelivery(Protocol):
    """Contract adapter gửi brief. Implement bởi bot (Discord); test dùng fake."""

    def is_ready(self, phase: str) -> bool: ...

    async def deliver(
        self, brief: Any, *, phase: str, agenda_summary: str | None = None
    ) -> None: ...


class BriefingListener:
    """Subscribe BriefingRequestedEvent và execute briefing pipeline."""

    def __init__(
        self,
        user_id: str,
        delivery: BriefDelivery | None = None,
        agenda_service_factory: object | None = None,
    ) -> None:
        self._delivery = delivery
        self._user_id = user_id
        # Wave B: callable(session) -> AgendaService | None
        # Injected from bootstrap so BriefingService can include agenda context.
        self._agenda_service_factory = agenda_service_factory

    def set_client(self, client: object) -> None:
        """Compat: chuyển client cho delivery adapter (bot on_ready gọi qua bootstrap getter)."""
        setter = getattr(self._delivery, "set_client", None)
        if callable(setter):
            setter(client)
        else:
            logger.warning(
                "briefing_listener.set_client_ignored", reason="delivery không nhận client"
            )

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
        """Cache a compact agenda summary string + structured buckets for the user.

        Uses only the event payload (no DB/AI calls) so this handler is cheap
        and safe to run before Morning Brief. The cached block is later
        prepended to the brief embed description for visual cohesion, while
        buckets are consumed by BriefingService for domain-level DECIDE mapping.
        """
        try:
            # Basic guard: if there is literally nothing, clear cache and return.
            if event.decide_count <= 0 and event.watch_count <= 0 and event.defer_count <= 0:
                set_agenda(event.user_id, None)
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

            # Wave B.1: build AgendaBuckets best-effort from event payload.
            buckets = AgendaBuckets(
                decide=list(event.decide_tickers or []),
                watch=list(event.watch_tickers or []),
                defer=[],  # defer tickers chưa có trong event schema P1
            )

            set_agenda(event.user_id, summary, buckets=buckets)

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

        if self._delivery is None or not self._delivery.is_ready(phase):
            logger.warning("briefing_listener.delivery_not_ready", phase=phase)
            return

        try:
            async with AsyncSessionLocal() as session:
                # Wave B: build AgendaService instance for this session if factory is wired.
                agenda_svc = (
                    self._agenda_service_factory(session)  # type: ignore[operator]
                    if self._agenda_service_factory is not None
                    else None
                )

                quote_svc = get_quote_service()
                pnl_cls = get_pnl_service()
                pnl_svc = pnl_cls(session=session, quote_service=quote_svc)

                svc = BriefingService(
                    watchlist_service=WatchlistService(session=session),
                    quote_service=quote_svc,
                    briefing_agent=get_briefing_agent(),
                    pnl_service=pnl_svc,
                    session=session,
                    sector_agent=get_sector_rotation_agent(),
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

            # P1/P1.5: agenda summary (nếu có) được adapter prepend lên brief.
            cached = get_agenda(self._user_id)
            agenda_summary = cached.summary if cached is not None and cached.summary else None
            await self._delivery.deliver(
                brief_result.output, phase=phase, agenda_summary=agenda_summary
            )
            logger.info(
                "briefing_listener.sent",
                phase=phase,
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
