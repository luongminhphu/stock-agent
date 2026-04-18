"""SnapshotScheduler — writes ThesisSnapshot records on a daily schedule.

Owner: market segment (price concern) + thin write into thesis domain.

Schedule: weekdays at 15:10 ICT (08:10 UTC) — 5 min after market close.

Design rules:
- Scheduler owns ONLY the timing and orchestration.
- Price fetching: QuoteService (market segment).
- DB write: direct SQLAlchemy session — creates ThesisSnapshot rows.
- Does NOT call ThesisService; snapshots are append-only side-effects.
- Skips tickers where QuoteService raises (logs warning, continues).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from discord.ext import tasks

from src.platform.logging import get_logger

logger = get_logger(__name__)

# Weekdays at 08:10 UTC = 15:10 ICT
_SNAPSHOT_TIME_UTC = datetime.strptime("08:10", "%H:%M").time().replace()


class SnapshotScheduler:
    """Attach to the Discord bot client for lifecycle management.

    Usage in bot on_ready:
        scheduler = SnapshotScheduler()
        scheduler.start()
    """

    def __init__(self) -> None:
        self._task = tasks.loop(
            time=__import__("datetime").time(8, 10, 0,
                tzinfo=__import__("datetime").timezone.utc)
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
        """Fetch active thesis tickers, get prices, write snapshots."""
        from src.platform.bootstrap import get_quote_service
        from src.platform.db import AsyncSessionLocal
        from src.thesis.models import Thesis, ThesisSnapshot, ThesisStatus
        from sqlalchemy import select

        logger.info("market.snapshot_scheduler.run_start")
        qs = get_quote_service()  # type: ignore[assignment]

        async with AsyncSessionLocal() as session:
            # 1. Load all active theses with entry_price set
            result = await session.execute(
                select(Thesis).where(
                    Thesis.status == ThesisStatus.ACTIVE,
                    Thesis.entry_price.is_not(None),
                )
            )
            theses = result.scalars().all()

            if not theses:
                logger.info("market.snapshot_scheduler.no_active_theses")
                return

            # 2. Bulk fetch prices
            tickers = list({t.ticker for t in theses})
            try:
                quotes = await qs.get_bulk_quotes(tickers)  # type: ignore[attr-defined]
                price_map: dict[str, float] = {q.ticker: q.price for q in quotes}
            except Exception as exc:
                logger.error(
                    "market.snapshot_scheduler.bulk_fetch_failed", error=str(exc)
                )
                return

            # 3. Write snapshots
            now = datetime.now(timezone.utc)
            written = 0
            for thesis in theses:
                price = price_map.get(thesis.ticker)
                if price is None:
                    logger.warning(
                        "market.snapshot_scheduler.missing_price",
                        ticker=thesis.ticker,
                    )
                    continue

                pnl_pct: float | None = None
                if thesis.entry_price and thesis.entry_price > 0:
                    pnl_pct = (price - thesis.entry_price) / thesis.entry_price * 100

                snap = ThesisSnapshot(
                    thesis_id=thesis.id,
                    price_at_snapshot=price,
                    pnl_pct=pnl_pct,
                    score_at_snapshot=thesis.score,
                    snapshotted_at=now,
                )
                session.add(snap)
                written += 1

            await session.commit()
            logger.info(
                "market.snapshot_scheduler.run_done",
                snapshots_written=written,
                total_theses=len(theses),
            )
