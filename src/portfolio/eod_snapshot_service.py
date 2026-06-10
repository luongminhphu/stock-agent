"""EodSnapshotService — write end-of-day P&L snapshot per position to DB.

Owner: portfolio segment.

Called by: bot.EodPortfolioSnapshotScheduler at 15:20 ICT weekdays.
Reads from: PortfolioRepository (open positions) + QuoteService (closing prices).
Writes to: position_daily_snapshots (upsert — safe to re-run same day).

Design rules:
  - One row per (user_id, ticker, snapshot_date). UPSERT on conflict.
  - snapshot_date = today ICT (UTC+7), NOT UTC.
  - close_price = last quote from QuoteService (should still be in cache at 15:20).
  - If QuoteService fails for a ticker → skip that ticker, log warning, continue rest.
  - Never raises — all errors are caught and returned in `errors` dict.
  - Returns SnapshotResult with written/skipped counts for observability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Protocol, runtime_checkable

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.portfolio.models import Position, PositionDailySnapshot
from src.portfolio.repository import PortfolioRepository

logger = get_logger(__name__)

_ICT = timedelta(hours=7)


def _today_ict() -> date:
    """Return today's date in ICT (UTC+7)."""
    return (datetime.now(UTC) + _ICT).date()


@runtime_checkable
class QuoteServiceProtocol(Protocol):
    """Minimal contract EodSnapshotService needs from market segment."""

    async def get_quote(self, ticker: str) -> object: ...


@dataclass
class SnapshotResult:
    """Result of a single record_eod_snapshot() call."""

    user_id: str
    snapshot_date: date
    written: int = 0
    skipped: int = 0
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.written + self.skipped


class EodSnapshotService:
    """Write EOD P&L snapshots for all open positions of a user.

    Usage::

        svc = EodSnapshotService(session=session, quote_service=get_quote_service())
        result = await svc.record_eod_snapshot(user_id="123456")
    """

    def __init__(
        self,
        session: AsyncSession,
        quote_service: QuoteServiceProtocol,
    ) -> None:
        if not isinstance(quote_service, QuoteServiceProtocol):
            raise TypeError(
                f"quote_service must implement QuoteServiceProtocol, "
                f"got: {type(quote_service).__name__}"
            )
        self._session = session
        self._repo = PortfolioRepository(session)
        self._quote_service = quote_service

    async def record_eod_snapshot(self, user_id: str) -> SnapshotResult:
        """Fetch all open positions + closing prices → upsert snapshots.

        Safe to re-run: UPSERT on (user_id, ticker, snapshot_date).
        Skips tickers where QuoteService fails — logs warning, continues rest.

        Returns SnapshotResult with written/skipped/errors counts.
        """
        snap_date = _today_ict()
        result = SnapshotResult(user_id=user_id, snapshot_date=snap_date)

        positions = await self._repo.list_open_positions(user_id)
        if not positions:
            logger.info("eod_snapshot.no_open_positions", user_id=user_id, date=str(snap_date))
            return result

        for pos in positions:
            try:
                close_price = await self._fetch_close_price(pos)
            except Exception as exc:
                err_msg = str(exc)
                logger.warning(
                    "eod_snapshot.price_fetch_failed",
                    ticker=pos.ticker,
                    error=err_msg,
                )
                result.errors[pos.ticker] = err_msg
                result.skipped += 1
                continue

            try:
                await self._upsert_snapshot(pos, close_price, snap_date)
                result.written += 1
                logger.debug(
                    "eod_snapshot.written",
                    ticker=pos.ticker,
                    close_price=close_price,
                    date=str(snap_date),
                )
            except Exception as exc:
                err_msg = str(exc)
                logger.error(
                    "eod_snapshot.upsert_failed",
                    ticker=pos.ticker,
                    error=err_msg,
                )
                result.errors[pos.ticker] = err_msg
                result.skipped += 1

        await self._session.commit()
        logger.info(
            "eod_snapshot.completed",
            user_id=user_id,
            date=str(snap_date),
            written=result.written,
            skipped=result.skipped,
            errors=list(result.errors.keys()),
        )
        return result

    async def get_latest_snapshots(
        self, user_id: str
    ) -> list[PositionDailySnapshot]:
        """Return most recent snapshot per ticker for a user.

        Used by readmodel route as primary source for portfolio dashboard.
        Returns one row per ticker (latest snapshot_date).
        """
        # Subquery: max snapshot_date per (user_id, ticker)
        from sqlalchemy import func

        subq = (
            select(
                PositionDailySnapshot.ticker,
                func.max(PositionDailySnapshot.snapshot_date).label("max_date"),
            )
            .where(PositionDailySnapshot.user_id == user_id)
            .group_by(PositionDailySnapshot.ticker)
            .subquery()
        )

        stmt = (
            select(PositionDailySnapshot)
            .join(
                subq,
                (PositionDailySnapshot.ticker == subq.c.ticker)
                & (PositionDailySnapshot.snapshot_date == subq.c.max_date),
            )
            .where(PositionDailySnapshot.user_id == user_id)
            .order_by(PositionDailySnapshot.ticker)
        )
        rows = await self._session.execute(stmt)
        return list(rows.scalars().all())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _fetch_close_price(self, position: Position) -> float:
        """Fetch closing price from QuoteService. Raises on failure."""
        quote = await self._quote_service.get_quote(position.ticker)
        return float(quote.price)  # type: ignore[union-attr]

    async def _upsert_snapshot(
        self,
        position: Position,
        close_price: float,
        snap_date: date,
    ) -> None:
        """Upsert one PositionDailySnapshot row — idempotent."""
        cost_basis = position.avg_cost * position.qty
        market_value = close_price * position.qty
        unrealized_pnl = (close_price - position.avg_cost) * position.qty
        unrealized_pct = (unrealized_pnl / cost_basis * 100) if cost_basis else 0.0

        values = {
            "user_id": position.user_id,
            "ticker": position.ticker,
            "snapshot_date": snap_date,
            "qty": position.qty,
            "avg_cost": position.avg_cost,
            "close_price": close_price,
            "cost_basis": cost_basis,
            "market_value": market_value,
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pct": round(unrealized_pct, 4),
            "thesis_id": position.thesis_id,
            "created_at": datetime.now(UTC),
        }

        stmt = (
            pg_insert(PositionDailySnapshot)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_position_daily_snapshot",
                set_={
                    "qty": values["qty"],
                    "avg_cost": values["avg_cost"],
                    "close_price": values["close_price"],
                    "cost_basis": values["cost_basis"],
                    "market_value": values["market_value"],
                    "unrealized_pnl": values["unrealized_pnl"],
                    "unrealized_pct": values["unrealized_pct"],
                    "thesis_id": values["thesis_id"],
                    "created_at": values["created_at"],
                },
            )
        )
        await self._session.execute(stmt)
