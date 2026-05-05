"""SnapshotScheduler — triggers daily thesis price snapshots after market close.

Owner: market segment (timing + price fetching only).

Schedule: weekdays at 15:10 ICT (08:10 UTC) — 5 min after HOSE close.

Design rules:
- Scheduler owns ONLY timing and price fetching (market concern).
- ThesisSnapshot write logic lives in thesis.ThesisSnapshotService.
- Scheduler fetches bulk prices, passes price_map to ThesisSnapshotService.
- Does NOT import Thesis / ThesisSnapshot models directly.
"""

from __future__ import annotations

from discord.ext import tasks

from src.platform.logging import get_logger

logger = get_logger(__name__)


class SnapshotScheduler:
    """Attach to the Discord bot client for lifecycle management.

    Usage in bot on_ready:
        scheduler = SnapshotScheduler()
        scheduler.start()
    """

    def __init__(self) -> None:
        self._task = tasks.loop(
            time=__import__("datetime").time(8, 10, 0, tzinfo=__import__("datetime").timezone.utc)
        )(self._run_snapshot)

    def start(self) -> None:
        if not self._task.is_running():
            self._task.start()
            logger.info("market.snapshot_scheduler.started", time_utc="08:10")

    def stop(self) -> None:
        self._task.cancel()
        logger.info("market.snapshot_scheduler.stopped")

    # ------------------------------------------------------------------
    # Core job
    # ------------------------------------------------------------------

    async def _run_snapshot(self) -> None:
        """Fetch live prices for active thesis tickers, delegate snapshot writes to thesis segment."""
        from sqlalchemy import select

        from src.platform.bootstrap import get_quote_service
        from src.platform.db import AsyncSessionLocal
        from src.thesis.models import Thesis, ThesisStatus
        from src.thesis.snapshot_service import ThesisSnapshotService

        logger.info("market.snapshot_scheduler.run_start")
        qs = get_quote_service()  # type: ignore[assignment]

        async with AsyncSessionLocal() as session:
            # 1. Resolve tickers that need snapshots (read-only, minimal import)
            result = await session.execute(
                select(Thesis.ticker).where(
                    Thesis.status == ThesisStatus.ACTIVE,
                    Thesis.entry_price.is_not(None),
                )
            )
            tickers = list({row[0] for row in result.all()})

            if not tickers:
                logger.info("market.snapshot_scheduler.no_active_tickers")
                return

            # 2. Bulk fetch prices — market segment concern
            try:
                quotes = await qs.get_bulk_quotes(tickers)  # type: ignore[attr-defined]
                price_map: dict[str, float] = {q.ticker: q.price for q in quotes}
            except Exception as exc:
                logger.error("market.snapshot_scheduler.bulk_fetch_failed", error=str(exc))
                return

            # 3. Delegate write to thesis segment
            snapshot_svc = ThesisSnapshotService(session)
            written = await snapshot_svc.record_daily_snapshots(price_map)

            logger.info(
                "market.snapshot_scheduler.run_done",
                snapshots_written=written,
                tickers_fetched=len(tickers),
            )
