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

import asyncio
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

# Timeout cho moi lan fetch quote. vnstock khong co timeout built-in —
# neu adapter hang, khong de caller (PUT edit/buy/sell, EOD job) treo vo han.
_QUOTE_TIMEOUT_SECS = 8.0


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

    async def refresh_today_snapshot(self, position: Position) -> bool:
        """Cập nhật snapshot HÔM NAY của 1 ticker sau khi position thay đổi ngoài EOD.

        Dùng khi user buy/sell/edit/adjust position trong ngày: dashboard đọc
        get_latest_snapshots() (max date / ticker). Nếu chỉ update khi ĐÃ có
        row hôm nay thì trước 15:20 dashboard vẫn hiện snapshot hôm qua —
        sửa qty/avg_cost "thành công" nhưng bảng không đổi.

        - Có snapshot hôm nay → upsert qty/avg_cost (giữ close_price nếu quote fail).
        - Chưa có row hôm nay → TẠO mới (close_price từ quote, hoặc snapshot
          gần nhất, hoặc avg_cost). Không còn return False im lặng.
        - Không có quote realtime → fallback close_price như trên.

        Returns True nếu đã ghi snapshot. Never raises — lỗi quote chỉ log.
        """
        today = _today_ict()
        existing = await self._session.execute(
            select(PositionDailySnapshot).where(
                PositionDailySnapshot.user_id == position.user_id,
                PositionDailySnapshot.ticker == position.ticker,
                PositionDailySnapshot.snapshot_date == today,
            )
        )
        snap = existing.scalar_one_or_none()

        close_price = snap.close_price if snap is not None else None
        try:
            close_price = await self._fetch_close_price(position)
        except Exception as exc:
            logger.warning(
                "eod_snapshot.refresh_quote_failed",
                ticker=position.ticker,
                error=str(exc),
            )
            if close_price is None:
                close_price = await self._latest_close_price(position)
            if close_price is None:
                close_price = float(position.avg_cost)

        await self._upsert_snapshot(position, float(close_price), today)
        logger.info(
            "eod_snapshot.refreshed",
            ticker=position.ticker,
            qty=position.qty,
            avg_cost=position.avg_cost,
            created=snap is None,
        )
        return True

    async def refresh_after_trade(
        self,
        user_id: str,
        ticker: str,
        position_closed: bool = False,
    ) -> bool:
        """Refresh snapshot hôm nay sau buy/sell — dashboard phản ánh ngay.

        - position_closed=True (full sell) → XOÁ snapshot hôm nay của ticker
          để dashboard không còn hiển thị vị thế đã đóng.
        - Ngược lại → re-fetch position hiện tại và upsert snapshot.

        Returns True nếu snapshot bị thay đổi. Never raises — lỗi chỉ log,
        trade vẫn thành công (snapshot sẽ được EOD job sửa lại 15:20).
        """
        ticker = ticker.upper()
        today = _today_ict()
        try:
            if position_closed:
                await self._session.execute(
                    delete(PositionDailySnapshot).where(
                        PositionDailySnapshot.user_id == user_id,
                        PositionDailySnapshot.ticker == ticker,
                        PositionDailySnapshot.snapshot_date == today,
                    )
                )
                logger.info("eod_snapshot.removed_closed", ticker=ticker, date=str(today))
                return True

            position = await self._repo.get_open_position(user_id, ticker)
            if position is None:
                return False
            return await self.refresh_today_snapshot(position)
        except Exception as exc:
            logger.warning(
                "eod_snapshot.refresh_after_trade_failed",
                ticker=ticker,
                error=str(exc),
            )
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _latest_close_price(self, position: Position) -> float | None:
        """close_price của snapshot gần nhất cho ticker — None nếu chưa từng snapshot."""
        result = await self._session.execute(
            select(PositionDailySnapshot.close_price)
            .where(
                PositionDailySnapshot.user_id == position.user_id,
                PositionDailySnapshot.ticker == position.ticker,
            )
            .order_by(PositionDailySnapshot.snapshot_date.desc())
            .limit(1)
        )
        value = result.scalar_one_or_none()
        return float(value) if value is not None else None

    async def _fetch_close_price(self, position: Position) -> float:
        """Fetch closing price from QuoteService. Raises on failure/timeout.

        asyncio.TimeoutError sau _QUOTE_TIMEOUT_SECS — caller bat nhu moi
        loi quote khac (fallback close cu / skip ticker), khong bao gio treo.
        """
        quote = await asyncio.wait_for(
            self._quote_service.get_quote(position.ticker),
            timeout=_QUOTE_TIMEOUT_SECS,
        )
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
