"""Thesis daily snapshot job — fetch live prices cho active thesis rồi ghi ThesisSnapshot.

Owner: thesis segment (boundary fix B6, chuyển từ market.snapshot_scheduler).

market là upstream — không được import thesis. Job này *dùng* market (quote_service)
và *ghi* thesis (ThesisSnapshotService), nên owner đúng là thesis. Scheduling
(discord tasks.loop) vẫn ở ``market.snapshot_scheduler.SnapshotScheduler`` dưới dạng
wrapper nhận ``job`` callable — bootstrap wire hai bên.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.platform.logging import get_logger

logger = get_logger(__name__)


async def run_snapshot_job(quote_service: object, session_factory: Callable[[], Any]) -> int:
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

    logger.info("thesis.snapshot_job.run_start")

    async with session_factory() as session:
        snapshot_svc = ThesisSnapshotService(session)

        # 1. Resolve tickers — thesis segment owns this query
        tickers = await snapshot_svc.get_active_tickers()

        if not tickers:
            logger.info("thesis.snapshot_job.no_active_tickers")
            return 0

        # 2. Bulk fetch prices — market segment concern
        try:
            quotes = await quote_service.get_bulk_quotes(tickers)  # type: ignore[attr-defined]
            price_map: dict[str, float] = {q.ticker: q.price for q in quotes}
        except Exception as exc:
            logger.error("thesis.snapshot_job.bulk_fetch_failed", error=str(exc))
            return 0

        # 3. Delegate write to thesis segment
        written = await snapshot_svc.record_daily_snapshots(price_map)

        logger.info(
            "thesis.snapshot_job.run_done",
            snapshots_written=written,
            tickers_fetched=len(tickers),
        )
        return written
