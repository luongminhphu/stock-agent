"""Discord tasks.loop wrapper cho snapshot job (weekdays 15:10 ICT = 08:10 UTC).

Owner: market segment (scheduling shim only).

Boundary fix B6: job logic (``run_snapshot_job``) đã chuyển sang
``src.thesis.snapshot_job`` vì nó ghi ThesisSnapshot. Scheduler này generic:
nhận ``job: Callable[[], Awaitable[int]]`` do bootstrap wire, không import thesis.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.platform.logging import get_logger

logger = get_logger(__name__)


class SnapshotScheduler:
    """Thin Discord shim wrapping a job callable in a tasks.loop.

    Usage in bootstrap:
        scheduler = SnapshotScheduler(job=lambda: run_snapshot_job(quote_service, AsyncSessionLocal))
    """

    def __init__(self, job: Callable[[], Awaitable[int]]) -> None:
        import datetime

        from discord.ext import tasks

        self._job = job
        self._task = tasks.loop(time=datetime.time(8, 10, 0, tzinfo=datetime.UTC))(self._run)

    def start(self) -> None:
        if not self._task.is_running():
            self._task.start()
            logger.info("market.snapshot_scheduler.started", time_utc="08:10")

    def stop(self) -> None:
        self._task.cancel()
        logger.info("market.snapshot_scheduler.stopped")

    async def run_once(self) -> int:
        """Manual trigger — chạy job ngay và trả số snapshot đã ghi."""
        return await self._job()

    async def _run(self) -> None:
        """Scheduled loop callback — return value intentionally discarded."""
        await self._job()
