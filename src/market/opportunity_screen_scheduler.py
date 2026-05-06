"""Opportunity Screen Scheduler — thin Discord shim for market segment.

Owner: market segment (job scheduling only).
Pattern mirrors SnapshotScheduler exactly — see snapshot_scheduler.py.

Schedule: weekdays 09:10 ICT (02:10 UTC) — 10 min after HOSE open.
Rationale: Give market 10 minutes to settle after open before scanning
  for real breakout/momentum candidates vs open-bell noise.

Lifecycle:
    scheduler = OpportunityScreenScheduler(quote_service)
    scheduler.start()   ← called in bot on_ready, AFTER bootstrap()
    scheduler.stop()    ← called in bot on_close

Manual trigger (bot command)::
    result = await scheduler.run_once()

Note: discord.ext.tasks import is deferred to __init__ so this module
can be imported in test environments without a Discord installation.
"""
from __future__ import annotations

from src.platform.logging import get_logger

logger = get_logger(__name__)


class OpportunityScreenScheduler:
    """Thin Discord shim that wraps run_opportunity_screen_job in tasks.loop.

    Usage in bot on_ready::
        scheduler = get_opportunity_screen_scheduler()
        scheduler.start()
    """

    def __init__(self, quote_service: object) -> None:
        import datetime

        from discord.ext import tasks

        self._quote_service = quote_service
        self._task = tasks.loop(
            time=datetime.time(2, 10, 0, tzinfo=datetime.timezone.utc)  # 09:10 ICT
        )(self._run)

    def start(self) -> None:
        if not self._task.is_running():
            self._task.start()
            logger.info("opportunity_screen_scheduler.started", time_utc="02:10")

    def stop(self) -> None:
        self._task.cancel()
        logger.info("opportunity_screen_scheduler.stopped")

    async def run_once(self) -> object:
        """Manual trigger — runs the screen job immediately.

        Returns ScreenResult. Use from bot commands or admin tools.
        """
        from src.market.opportunity_screen_service import run_opportunity_screen_job

        return await run_opportunity_screen_job(self._quote_service)

    async def _run(self) -> None:
        """Scheduled loop callback."""
        from src.market.opportunity_screen_service import run_opportunity_screen_job

        await run_opportunity_screen_job(self._quote_service)
