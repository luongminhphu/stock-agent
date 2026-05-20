"""AgendaScheduler — runs AgendaService for one or all active users.

Owner: briefing segment (schedule trigger owned by bot/scheduler adapter).
Pattern: stateless — instantiate once per cron run, then discard.
Suggested cron: 07:30 ICT daily (before morning briefing).

No domain logic here. All rules live in AgendaService + AgendaBuilderAgent.
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
        """Build agenda for one user and push via notifier if available."""
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
