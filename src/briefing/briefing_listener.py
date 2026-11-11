"""BriefingListener — Event-driven bridge from BriefingRequestedEvent → BriefingService.

Owner: briefing segment.
Subscribes: BriefingRequestedEvent
Emits:      BriefingReadyEvent (consumed by readmodel.CacheSubscriber for cache invalidation)

Boundary (boundary fix B5):
- Listener nhận event, resolve deps, gọi BriefingService, rồi giao kết quả cho
  ``BriefDelivery`` (protocol dưới đây). briefing quyết định *gửi gì*; adapter
  (``bot.brief_delivery.DiscordBriefDelivery``) quyết định *gửi ở đâu, dạng gì*.
- Không chứa logic generate brief — đó là BriefingService / BriefingAgent.
- Không import discord hay src.bot — import-linter contract domain-no-adapters.
"""

from __future__ import annotations

from typing import Any, Protocol

from src.platform.event_bus import get_event_bus
from src.platform.events import (
    BriefingReadyEvent,
    BriefingRequestedEvent,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)


class BriefDelivery(Protocol):
    """Contract adapter gửi brief. Implement bởi bot (Discord); test dùng fake."""

    def is_ready(self, phase: str) -> bool: ...

    async def deliver(self, brief: Any, *, phase: str) -> None: ...


class BriefingListener:
    """Subscribe BriefingRequestedEvent và execute briefing pipeline."""

    def __init__(
        self,
        user_id: str,
        delivery: BriefDelivery | None = None,
        **_legacy: Any,
    ) -> None:
        self._delivery = delivery
        self._user_id = user_id

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
        logger.info("briefing_listener.registered")

    async def _handle(self, event: BriefingRequestedEvent) -> None:
        from src.briefing.service import BriefingService
        from src.platform.bootstrap import (
            get_briefing_agent,
            get_pnl_service,
            get_quote_service,
        )
        from src.platform.db import AsyncSessionLocal
        from src.watchlist.service import WatchlistService

        phase = event.brief_type  # "morning" | "eod"

        if self._delivery is None or not self._delivery.is_ready(phase):
            logger.warning("briefing_listener.delivery_not_ready", phase=phase)
            return

        try:
            async with AsyncSessionLocal() as session:
                quote_svc = get_quote_service()
                pnl_cls = get_pnl_service()
                pnl_svc = pnl_cls(session=session, quote_service=quote_svc)

                svc = BriefingService(
                    watchlist_service=WatchlistService(session=session),
                    quote_service=quote_svc,
                    briefing_agent=get_briefing_agent(),
                    pnl_service=pnl_svc,
                    session=session,
                )
                if phase == "morning":
                    brief_result = await svc.generate_morning_brief(user_id=self._user_id)
                else:
                    brief_result = await svc.generate_eod_brief(user_id=self._user_id)
                await session.commit()

            logger.info(
                "briefing_listener.brief_generated",
                phase=phase,
            )

            await self._delivery.deliver(brief_result.output, phase=phase)
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
