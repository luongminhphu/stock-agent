"""PersistentOHLCVAdapter — lịch sử nến ngày lưu DB, chỉ tải phần còn thiếu.

Owner: market segment.

Vì sao:
    Base-rate cần ~10 năm nến ngày/mã. Gọi lại toàn bộ lịch sử từ API của CTCK mỗi lần
    phân tích là cách nhanh nhất để bị giới hạn tốc độ hoặc chặn. Adapter này bọc adapter
    gốc (VCI): lần đầu tải trọn khoảng, sau đó mỗi ngày chỉ tải vài phiên cuối.

Quy tắc:
    - Chỉ LƯU nến đã chốt phiên (ngày < hôm nay, hoặc hôm nay sau giờ chốt 15:15 ICT).
      Nến phiên đang chạy luôn lấy trực tiếp (khoảng nhỏ) và không ghi DB.
    - Tải phần đuôi có chồng lấn OVERLAP_DAYS ngày. Nếu nến cũ đã lưu lệch với bản mới
      > ADJUST_TOLERANCE → nguồn đã điều chỉnh giá (chia cổ tức/thưởng) → tải lại toàn
      bộ lịch sử của mã để chuỗi giá điều chỉnh nhất quán.
    - Lỗi DB không chặn: rơi về gọi thẳng adapter gốc.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from src.market.ohlcv_service import (
    _SESSION_FINAL_TIME,
    Candle,
    Interval,
    OHLCVAdapter,
    _now_ict,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

OVERLAP_DAYS = 10
ADJUST_TOLERANCE = 0.005  # 0.5%
_UPSERT_CHUNK = 500


def last_closed_session_date(now: datetime | None = None) -> date:
    """Ngày gần nhất mà nến D1 đã chốt (không kiểm tra ngày lễ — dữ liệu tự rỗng)."""
    now = now or _now_ict()
    return now.date() if now.time() >= _SESSION_FINAL_TIME else now.date() - timedelta(days=1)


def detect_adjustment(stored: dict[date, float], fresh: list[Candle]) -> bool:
    """True nếu nến cũ đã lưu khác bản mới tải (nguồn đã điều chỉnh lịch sử giá)."""
    for c in fresh:
        old = stored.get(c.date)
        if old and c.close and abs(c.close / old - 1) > ADJUST_TOLERANCE:
            return True
    return False


class PersistentOHLCVAdapter(OHLCVAdapter):
    def __init__(self, inner: OHLCVAdapter, session_factory: Any) -> None:
        self._inner = inner
        self._sf = session_factory

    async def close(self) -> None:
        await self._inner.close()

    async def fetch_candles(
        self,
        ticker: str,
        from_date: date,
        to_date: date,
        interval: Interval = Interval.D1,
    ) -> list[Candle]:
        if interval != Interval.D1 or self._sf is None:
            return await self._inner.fetch_candles(ticker, from_date, to_date, interval)
        ticker = ticker.upper()
        closed_until = min(to_date, last_closed_session_date())
        try:
            if from_date <= closed_until:
                await self._sync(ticker, from_date, closed_until)
            candles = await self._load(ticker, from_date, closed_until)
        except Exception as exc:  # noqa: BLE001 — DB lỗi → gọi thẳng nguồn
            logger.warning("candle_store.db_failed", ticker=ticker, error=str(exc))
            return await self._inner.fetch_candles(ticker, from_date, to_date, interval)
        if to_date > closed_until:  # phiên đang chạy: khoảng nhỏ, không lưu
            live = await self._inner.fetch_candles(
                ticker, closed_until + timedelta(days=1), to_date, interval
            )
            candles.extend(c for c in live if c.date > closed_until)
        return candles

    # ── internals ──────────────────────────────────────────────────────────

    async def _sync(self, ticker: str, from_date: date, until: date) -> None:
        from src.market.models import CandleCoverage  # noqa: PLC0415

        async with self._sf() as session:
            cov = await session.get(CandleCoverage, ticker)
            first, last = (cov.first_date, cov.last_date) if cov else (None, None)

        if first is None or last is None or from_date < first:
            fresh = await self._inner.fetch_candles(ticker, from_date, until)
            await self._save(
                ticker,
                fresh,
                from_date if first is None else min(from_date, first),
                max(until, last or until),
            )
            logger.info("candle_store.backfill", ticker=ticker, bars=len(fresh))
            return
        if until <= last:
            return

        tail_from = last - timedelta(days=OVERLAP_DAYS)
        fresh = await self._inner.fetch_candles(ticker, tail_from, until)
        stored = await self._closes(ticker, tail_from, last)
        if detect_adjustment(stored, fresh):
            logger.info("candle_store.adjustment_detected", ticker=ticker)
            full = await self._inner.fetch_candles(ticker, first, until)
            await self._replace(ticker, full, first, until)
            return
        await self._save(ticker, fresh, first, until)

    async def _closes(self, ticker: str, start: date, end: date) -> dict[date, float]:
        from sqlalchemy import select  # noqa: PLC0415

        from src.market.models import DailyCandle  # noqa: PLC0415

        async with self._sf() as session:
            rows = await session.execute(
                select(DailyCandle.date, DailyCandle.close)
                .where(DailyCandle.ticker == ticker)
                .where(DailyCandle.date >= start, DailyCandle.date <= end)
            )
            return {d: c for d, c in rows.all()}

    async def _load(self, ticker: str, start: date, end: date) -> list[Candle]:
        from sqlalchemy import select  # noqa: PLC0415

        from src.market.models import DailyCandle  # noqa: PLC0415

        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(DailyCandle)
                    .where(DailyCandle.ticker == ticker)
                    .where(DailyCandle.date >= start, DailyCandle.date <= end)
                    .order_by(DailyCandle.date)
                )
            ).scalars()
            return [
                Candle(
                    ticker=ticker,
                    date=r.date,
                    open=r.open,
                    high=r.high,
                    low=r.low,
                    close=r.close,
                    volume=r.volume,
                    value=0.0,
                )
                for r in rows
            ]

    async def _replace(self, ticker: str, candles: list[Candle], first: date, last: date) -> None:
        from sqlalchemy import delete  # noqa: PLC0415

        from src.market.models import DailyCandle  # noqa: PLC0415

        async with self._sf() as session:
            await session.execute(delete(DailyCandle).where(DailyCandle.ticker == ticker))
            await session.commit()
        await self._save(ticker, candles, first, last)

    async def _save(self, ticker: str, candles: list[Candle], first: date, last: date) -> None:
        from src.market.models import CandleCoverage, DailyCandle  # noqa: PLC0415
        from src.platform.db import upsert_rows  # noqa: PLC0415

        rows = [
            {
                "ticker": ticker,
                "date": c.date,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": int(c.volume),
            }
            for c in candles
            if c.date <= last
        ]
        for i in range(0, len(rows), _UPSERT_CHUNK):
            ok = await upsert_rows(
                self._sf,
                DailyCandle,
                rows[i : i + _UPSERT_CHUNK],
                conflict_columns=["ticker", "date"],
                log_event="candle_store.upsert_failed",
            )
            if not ok:
                raise RuntimeError("candle upsert failed")
        await upsert_rows(
            self._sf,
            CandleCoverage,
            {"ticker": ticker, "first_date": first, "last_date": last, "updated_at": _now_ict()},
            conflict_columns=["ticker"],
            log_event="candle_store.coverage_failed",
        )
