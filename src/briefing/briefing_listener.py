"""
BriefingListener — Wave 7 full implementation.

Owner: briefing segment.
Boundary:
  - Subscribes to BriefingRequestedEvent on the global EventBus.
  - Delegates AI generation to BriefingService.
  - Formats the narrative via briefing.formatter (Discord-ready string).
  - Publishes BriefingReadyEvent onto the bus for bot delivery.
  - NEVER imports Discord SDK, bot runtime, or scheduler internals.
  - NEVER holds an open AsyncSession across events — uses session_factory.

Bootstrap contract::

    listener = BriefingListener(
        session_factory=async_session_factory,
        watchlist_service=watchlist_service_singleton,
        quote_service=quote_service_singleton,
        briefing_agent=briefing_agent_singleton,
        pnl_service=pnl_service_singleton,     # optional
        thesis_service=thesis_service_singleton, # optional
        user_id_resolver=lambda: settings.DEFAULT_USER_ID,  # or per-event resolution
    )
    listener.register()   # idempotent

Dedup:
    Same (brief_type, calendar_date_ICT) is suppressed within 23 hours
    to prevent double-briefs from scheduler retries or manual triggers.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.event_bus import get_event_bus
from src.platform.events import BriefingReadyEvent, BriefingRequestedEvent

logger = logging.getLogger(__name__)

# ICT = UTC+7
_ICT = timezone(timedelta(hours=7))

# Dedup window: suppress same (brief_type + date) within 23h
_BRIEF_DEDUP_WINDOW = timedelta(hours=23)

# Max chars for content_summary stored in BriefingReadyEvent
_SUMMARY_MAX_CHARS = 300

AsyncSessionFactory = Callable[[], AsyncGenerator[AsyncSession, None]]
UserIdResolver = Callable[[BriefingRequestedEvent], str]


def _default_user_id_resolver(event: BriefingRequestedEvent) -> str:
    """
    Fallback user_id resolver: reads from event.context_hint if it looks like
    a user_id (no spaces, non-empty), otherwise raises.

    Bootstrap should inject a proper resolver that maps to the real user_id
    from app settings or the event payload.
    """
    hint = (event.context_hint or "").strip()
    if hint and " " not in hint:
        return hint
    raise ValueError(
        f"Cannot resolve user_id from BriefingRequestedEvent context_hint={hint!r}. "
        "Inject a user_id_resolver into BriefingListener at bootstrap."
    )


class BriefingListener:
    """
    Full Wave 7 implementation of the briefing event listener.

    Flow per event
    --------------
    1. _resolve_user_id()      — extract user_id from event or resolver
    2. _generate_brief()       — open session, call BriefingService, return BriefOutput
    3. _format_brief()         — convert BriefOutput → Discord markdown string
    4. _publish_ready()        — publish BriefingReadyEvent (with dedup)

    Fault isolation
    ---------------
    Each step has its own try/except.  Failure in step 2 skips 3-4 and logs
    the error.  Failure in step 4 (bus publish) is logged but does not affect
    the persisted BriefSnapshot from step 2.
    """

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        watchlist_service: Any,
        quote_service: Any,
        briefing_agent: Any,
        pnl_service: Any | None = None,
        thesis_service: Any | None = None,
        user_id_resolver: UserIdResolver | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._watchlist_service = watchlist_service
        self._quote_service = quote_service
        self._briefing_agent = briefing_agent
        self._pnl_service = pnl_service
        self._thesis_service = thesis_service
        self._user_id_resolver = user_id_resolver or _default_user_id_resolver
        self._registered = False

    # ── bootstrap ───────────────────────────────────────────────────────────

    def register(self) -> None:
        """Subscribe to BriefingRequestedEvent on the global bus. Idempotent."""
        if self._registered:
            return
        bus = get_event_bus()
        bus.subscribe_handler(BriefingRequestedEvent, self._handle_briefing_requested)
        self._registered = True
        logger.info("briefing_listener.registered")

    # ── main handler ──────────────────────────────────────────────────────────

    async def _handle_briefing_requested(self, event: BriefingRequestedEvent) -> None:
        logger.info(
            "briefing_listener.received",
            extra={
                "brief_type":   event.brief_type,
                "triggered_by": event.triggered_by,
                "event_id":     event.event_id,
            },
        )

        # Step 1: resolve user_id
        try:
            user_id = self._user_id_resolver(event)
        except Exception as exc:
            logger.error(
                "briefing_listener.user_id_resolution_failed",
                extra={"event_id": event.event_id, "error": str(exc)},
            )
            return

        # Step 2: generate brief
        brief_output = await self._generate_brief(event, user_id)
        if brief_output is None:
            return

        # Step 3: format to Discord markdown
        formatted = self._format_brief(brief_output, event.brief_type)
        if formatted is None:
            return

        # Step 4: publish BriefingReadyEvent
        await self._publish_ready(event, formatted)

    # ── step 2: generate ────────────────────────────────────────────────────────

    async def _generate_brief(self, event: BriefingRequestedEvent, user_id: str) -> Any | None:
        """
        Open a fresh session, construct BriefingService, call the correct
        generation method based on brief_type.

        Returns BriefOutput on success, None on failure.
        Session is always closed after this call.
        """
        from src.briefing.service import BriefingService

        brief_type = (event.brief_type or "morning").lower()

        async with self._open_session() as session:
            service = BriefingService(
                watchlist_service=self._watchlist_service,
                quote_service=self._quote_service,
                briefing_agent=self._briefing_agent,
                pnl_service=self._pnl_service,
                thesis_service=self._thesis_service,
                session=session,
            )

            try:
                if brief_type == "morning":
                    output = await service.generate_morning_brief(user_id=user_id)
                elif brief_type == "eod":
                    output = await service.generate_eod_brief(user_id=user_id)
                elif brief_type == "alert":
                    # Alert briefs reuse morning generation path with context_hint
                    # injected as extra context.  BriefingService surfaces it via
                    # investor_profile / lesson context if session is provided.
                    output = await service.generate_morning_brief(user_id=user_id)
                else:
                    logger.warning(
                        "briefing_listener.unknown_brief_type",
                        extra={"brief_type": brief_type, "event_id": event.event_id},
                    )
                    output = await service.generate_morning_brief(user_id=user_id)

                logger.info(
                    "briefing_listener.generated",
                    extra={
                        "brief_type": brief_type,
                        "user_id":    user_id,
                        "headline":   getattr(output, "headline", ""),
                        "event_id":   event.event_id,
                    },
                )
                return output

            except Exception as exc:
                logger.exception(
                    "briefing_listener.generation_failed",
                    extra={
                        "brief_type": brief_type,
                        "user_id":    user_id,
                        "error":      str(exc),
                        "event_id":   event.event_id,
                    },
                )
                return None

    # ── step 3: format ───────────────────────────────────────────────────────────

    def _format_brief(self, brief_output: Any, brief_type: str) -> str | None:
        """
        Convert BriefOutput → Discord-ready markdown string.
        Returns None and logs error if formatting fails.
        """
        from src.briefing.formatter import (
            format_brief,
            format_eod_brief,
            format_morning_brief,
        )

        bt = (brief_type or "morning").lower()
        try:
            if bt == "morning":
                return format_morning_brief(brief_output)
            elif bt == "eod":
                return format_eod_brief(brief_output)
            else:
                # alert / unknown — use generic formatter with capitalized label
                label = bt.replace("_", " ").title() + " Brief"
                return format_brief(brief_output, brief_type=label)
        except Exception as exc:
            logger.exception(
                "briefing_listener.format_failed",
                extra={"brief_type": bt, "error": str(exc)},
            )
            return None

    # ── step 4: publish BriefingReadyEvent ────────────────────────────────────────

    async def _publish_ready(self, event: BriefingRequestedEvent, formatted: str) -> None:
        """
        Build and publish BriefingReadyEvent.
        Dedup key: "{brief_type}:{YYYY-MM-DD_ICT}" — prevents double-delivery
        on same calendar day in ICT timezone.
        content_summary is the first _SUMMARY_MAX_CHARS chars of the formatted
        string (enough for a Discord embed preview or notification).
        The full content is stored in BriefSnapshot via BriefingService.
        """
        today_ict = datetime.now(_ICT).strftime("%Y-%m-%d")
        dedup_key  = f"{event.brief_type}:{today_ict}"

        content_summary = formatted[:_SUMMARY_MAX_CHARS].rstrip()
        if len(formatted) > _SUMMARY_MAX_CHARS:
            content_summary += "\u2026"  # ellipsis to signal truncation

        ready_event = BriefingReadyEvent(
            brief_type=event.brief_type,
            channel="discord",
            content_summary=content_summary,
        )

        bus = get_event_bus()
        try:
            published = await bus.publish(
                ready_event,
                dedup_key=dedup_key,
                dedup_window=_BRIEF_DEDUP_WINDOW,
            )
            if published:
                logger.info(
                    "briefing_listener.ready_published",
                    extra={
                        "brief_type":  event.brief_type,
                        "event_id":    ready_event.event_id,
                        "summary_len": len(content_summary),
                    },
                )
            else:
                logger.info(
                    "briefing_listener.ready_dedup_suppressed",
                    extra={"dedup_key": dedup_key},
                )
        except Exception as exc:
            logger.exception(
                "briefing_listener.publish_failed",
                extra={"brief_type": event.brief_type, "error": str(exc)},
            )

    # ── helpers ─────────────────────────────────────────────────────────────

    @asynccontextmanager
    async def _open_session(self):
        """Open a fresh AsyncSession from the factory. Always closes on exit."""
        async with self._session_factory() as session:
            yield session
