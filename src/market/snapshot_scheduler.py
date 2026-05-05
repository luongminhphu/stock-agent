"""Market snapshot job + Discord scheduler shim.

Owner: market segment (job logic only).

Two distinct concerns are separated here:

  run_snapshot_job(quote_service, session_factory)
      Pure async function. No Discord imports. Testable in isolation.
      Responsibilities:
        1. Open a DB session via session_factory.
        2. Resolve active tickers via ThesisSnapshotService.get_active_tickers()
           — market segment NEVER imports Thesis/ThesisStatus models directly.
        3. Bulk-fetch live prices from quote_service.
        4. Delegate snapshot writes to ThesisSnapshotService.record_daily_snapshots().
      Returns:
        int — number of snapshots written (0 if nothing to do or fetch failed).

  SnapshotScheduler
      Thin Discord shim. The ONLY place discord.ext.tasks is imported in this
      module. Wraps run_snapshot_job in a tasks.loop. Lifecycle methods
      (start/stop) are called by the bot on_ready / on_close handlers.

Schedule: weekdays at 15:10 ICT (08:10 UTC) — 5 min after HOSE close.
"""

from __future__ import annotations

from src.platform.logging import get_logger

logger = get_logger(__name__)


async def run_snapshot_job(quote_service: object, session_factory: object) -> int:
    """Fetch live prices for active thesis tickers and write daily snapshots.

    Args:
        quote_service:   QuoteService instance (duck-typed to avoid circular import).
        session_factory: Callable that returns an AsyncSession context manager
                         (e.g. AsyncSessionLocal from src.platform.db).

    Returns:
        Number of snapshots written. Returns 0 if there are no active tickers
        or if the bulk price fetch fails.

    This function is the testable core of the snapshot workflow.
    It contains zero Discord SDK imports.
    """
    from src.thesis.snapshot_service import ThesisSnapshotService

    logger.info("market.snapshot_job.run_start")

    async with session_factory() as session:  # type: ignore[attr-defined]
        snapshot_svc = ThesisSnapshotService(session)

        # 1. Resolve tickers — thesis segment owns this query
        tickers = await snapshot_svc.get_active_tickers()

        if not tickers:
            logger.info("market.snapshot_job.no_active_tickers")
            return 0

        # 2. Bulk fetch prices — market segment concern
        try:
            quotes = await quote_service.get_bulk_quotes(tickers)  # type: ignore[attr-defined]
            price_map: dict[str, float] = {q.ticker: q.price for q in quotes}
        except Exception as exc:
            logger.error("market.snapshot_job.bulk_fetch_failed", error=str(exc))
            return 0

        # 3. Delegate write to thesis segment
        written = await snapshot_svc.record_daily_snapshots(price_map)

        logger.info(
            "market.snapshot_job.run_done",
            snapshots_written=written,
            tickers_fetched=len(tickers),
        )
        return written


class SnapshotScheduler:
    """Thin Discord shim that wraps run_snapshot_job in a tasks.loop.

    Attach to the Discord bot client for lifecycle management.

    Usage in bot on_ready:
        scheduler = SnapshotScheduler(quote_service, AsyncSessionLocal)
        scheduler.start()
    """

    def __init__(self, quote_service: object, session_factory: object) -> None:
        from discord.ext import tasks
        import datetime

        self._quote_service = quote_service
        self._session_factory = session_factory
        self._task = tasks.loop(
            time=datetime.time(8, 10, 0, tzinfo=datetime.timezone.utc)
        )(self._run)

    def start(self) -> None:
        if not self._task.is_running():
            self._task.start()
            logger.info("market.snapshot_scheduler.started", time_utc="08:10")

    def stop(self) -> None:
        self._task.cancel()
        logger.info("market.snapshot_scheduler.stopped")

    async def run_once(self) -> int:
        """Manual trigger — runs the snapshot job immediately and returns the
        number of snapshots written.

        Use this from bot commands or admin tools instead of calling _run()
        directly. Safe to call outside the scheduled loop.
        """
        return await run_snapshot_job(self._quote_service, self._session_factory)

    async def _run(self) -> None:
        """Scheduled loop callback — return value intentionally discarded."""
        await run_snapshot_job(self._quote_service, self._session_factory)
