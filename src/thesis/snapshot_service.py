"""ThesisSnapshotService — daily price snapshot writes for active theses.

Owner: thesis segment.

Called by market.run_snapshot_job after it fetches live prices.
This service owns the full write concern:
  - Load active theses with entry_price set
  - Compute pnl_pct from price_map
  - Create ThesisSnapshot rows
  - Commit the session

Design rules:
- Accepts a pre-built price_map {ticker: price} so it does NOT import
  QuoteService or any market adapter. Market segment provides the prices.
- Session is created internally so the scheduler stays stateless.
- Skips theses whose ticker is not in price_map (logs warning).
- Returns the count of snapshots written for logging/monitoring.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.models import Thesis, ThesisSnapshot, ThesisStatus

logger = get_logger(__name__)


class ThesisSnapshotService:
    """Writes daily ThesisSnapshot records for all active theses.

    Inject a session per call (caller owns session lifecycle), or use
    the class method `run_with_new_session` for scheduler use-cases.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_tickers(self) -> list[str]:
        """Return unique tickers for all active theses that have an entry_price set.

        Called by market.run_snapshot_job so the market segment never needs
        to import Thesis / ThesisStatus models directly.

        Returns:
            Deduplicated list of uppercase ticker strings.
        """
        result = await self._session.execute(
            select(Thesis.ticker).where(
                Thesis.status == ThesisStatus.ACTIVE,
                Thesis.entry_price.is_not(None),
            )
        )
        return list({row[0] for row in result.all()})

    async def record_daily_snapshots(self, price_map: dict[str, float]) -> int:
        """Create ThesisSnapshot rows for active theses present in price_map.

        Args:
            price_map: {TICKER: price_in_VND} — provided by market segment.

        Returns:
            Number of snapshot rows written.
        """
        if not price_map:
            logger.info("thesis.snapshot_service.empty_price_map")
            return 0

        result = await self._session.execute(
            select(Thesis).where(
                Thesis.status == ThesisStatus.ACTIVE,
                Thesis.entry_price.is_not(None),
            )
        )
        theses = result.scalars().all()

        if not theses:
            logger.info("thesis.snapshot_service.no_active_theses")
            return 0

        now = datetime.now(UTC)
        written = 0

        for thesis in theses:
            price = price_map.get(thesis.ticker)
            if price is None:
                logger.warning(
                    "thesis.snapshot_service.missing_price",
                    ticker=thesis.ticker,
                    thesis_id=thesis.id,
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
            self._session.add(snap)
            written += 1

        if written:
            await self._session.commit()
            logger.info(
                "thesis.snapshot_service.committed",
                snapshots_written=written,
                total_active=len(theses),
            )

        return written
