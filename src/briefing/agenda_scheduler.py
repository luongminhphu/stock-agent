"""AgendaScheduler — runs AgendaService for one or all active users.

Owner: briefing segment (schedule trigger owned by bot/scheduler adapter).
Pattern: stateless — instantiate once per cron run, then discard.
Suggested cron: 07:30 ICT daily (before morning briefing).

No domain logic here. All rules live in AgendaService + AgendaBuilderAgent.

Wave B:
  After a successful build, emits DailyAgendaCompletedEvent so downstream
  consumers (BriefingService, bot notifier) can react without polling.
  Non-blocking — event publish failure never fails the agenda build.
"""
from __future__ import annotations

from src.platform.logging import get_logger

logger = get_logger(__name__)


class AgendaScheduler:
    """Orchestrates the daily agenda build for one or many users."""

    def __init__(self, agenda_service, bot_notifier=None) -> None:
        self._svc = agenda_service
        self._notifier = bot_notifier  # optional — push to Discord

    async def run_for_user(self, user_id: str) -> None:
        """Build agenda for one user, emit event, and push via notifier if available."""
        result = await self._svc.build_agenda(user_id)
        if result is None:
            logger.warning("agenda_scheduler.build_failed", user_id=user_id)
            return

        logger.info(
            "agenda_scheduler.done",
            user_id=user_id,
            decide=len(result.decide),
            watch=len(result.watch),
            defer=len(result.defer),
        )

        # Wave B: emit event so BriefingService and bot can react.
        await self._emit_event(user_id, result)

        if self._notifier is not None:
            await self._notifier.push_agenda(user_id, result)

    async def run_all(self, user_ids: list[str]) -> None:
        """Batch run — an error for one user does not block others."""
        for uid in user_ids:
            try:
                await self.run_for_user(uid)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "agenda_scheduler.user_failed",
                    user_id=uid,
                    error=str(exc),
                    exc_info=True,
                )

    async def _emit_event(self, user_id: str, result) -> None:
        """Publish DailyAgendaCompletedEvent to the event bus.

        Non-blocking: any failure is logged and swallowed so a bus error
        never prevents the agenda result from being delivered.
        """
        try:
            from src.platform.event_bus import get_event_bus  # noqa: PLC0415
            from src.platform.events import DailyAgendaCompletedEvent  # noqa: PLC0415

            decide_tickers = tuple(item.ticker for item in result.decide[:10])
            watch_tickers = tuple(item.ticker for item in result.watch[:10])

            event = DailyAgendaCompletedEvent(
                user_id=user_id,
                decide_count=len(result.decide),
                watch_count=len(result.watch),
                defer_count=len(result.defer),
                decide_tickers=decide_tickers,
                watch_tickers=watch_tickers,
                opening_line=getattr(result, "opening_line", "") or "",
            )
            bus = get_event_bus()
            await bus.publish(event)
            logger.info(
                "agenda_scheduler.event_emitted",
                user_id=user_id,
                decide_count=event.decide_count,
                watch_count=event.watch_count,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "agenda_scheduler.event_emit_failed",
                user_id=user_id,
                error=str(exc),
            )
