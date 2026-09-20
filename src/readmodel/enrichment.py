"""Readmodel enrichment inputs — price_map / position_map cho projection.

Owner: readmodel segment.

Truoc Wave A cac helper nay nam trong ``src/api/routes/readmodel.py`` (api
adapter). Chung la *input* cho projection (P&L, stop-loss proximity,
conviction timeline) nen thuoc readmodel, khong thuoc api.

Design rules:
- 1 bulk quote call per request (khong N+1).
- Loi market (MarketClosedError, network) -> tra {} de caller fallback
  (avg_cost / snapshot close). Khong bao gio raise ra route.
- ``list_thesis_tickers`` la query nhe (SELECT DISTINCT ticker) — dung de lay
  danh sach ticker can fetch gia THAY CHO viec goi full ``get_theses_list``
  2 lan (truoc day: 1 lan lay ticker, 1 lan lay du lieu that).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.bootstrap import get_quote_service, get_ticker_context_service
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.market.ticker_context import TickerContext

logger = get_logger(__name__)

PositionMap = dict[str, tuple[float, float]]
"""ticker -> (qty, avg_cost) cua vi the mo."""

MarketContextMap = dict[str, "TickerContext"]
"""ticker -> market.TickerContext (quote + MA/RSI/ATR/vol/52w + trend + source_quality)."""


async def build_market_context_map(tickers: list[str]) -> MarketContextMap:
    """Wave U2a: 1 bulk TickerContext fetch cho projection.

    Cung nguon du lieu ma AI review/pretrade/stop-breach dang dung (Wave C) —
    dashboard va AI nhin cung mot boi canh. Tra {} khi market dong hoac loi;
    caller fallback sang avg_cost / snapshot close nhu price_map.
    """
    if not tickers:
        return {}
    try:
        return await get_ticker_context_service().get_many(tickers)
    except Exception as exc:
        if type(exc).__name__ != "MarketClosedError":
            logger.warning(
                "readmodel.market_context_map.fetch_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        return {}


def price_map_from_context(context_map: MarketContextMap) -> dict[str, float]:
    """Derive price_map cu tu context_map — giu contract cho projection chua doc ctx."""
    return {t: c.price for t, c in context_map.items() if c.price}


async def build_price_map(tickers: list[str]) -> dict[str, float]:
    """Fetch gia hien tai cho danh sach tickers tu QuoteService.

    Tra {} khi market dong (MarketClosedError) hoac fetch that bai.
    Caller fallback sang avg_cost khi price_map thieu key.
    """
    if not tickers:
        return {}
    try:
        quote_svc = get_quote_service()
        quotes = await quote_svc.get_bulk_quotes(tickers)
        return {q.ticker: q.price for q in quotes if q.price}
    except Exception as exc:
        # Ngoai gio: im lang, caller se fallback sang avg_cost
        if type(exc).__name__ != "MarketClosedError":
            logger.warning(
                "readmodel.price_map.fetch_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        return {}


async def build_position_map(session: AsyncSession, user_id: str) -> PositionMap:
    """Load open positions cua user -> {ticker: (qty, avg_cost)}."""
    try:
        from src.portfolio.models import Position

        rows = (
            await session.execute(
                select(Position.ticker, Position.qty, Position.avg_cost).where(
                    Position.user_id == user_id,
                    Position.closed_at.is_(None),
                    Position.qty > 0,
                )
            )
        ).all()
        result: PositionMap = {}
        for p in rows:
            if p.ticker not in result:
                result[p.ticker] = (p.qty, p.avg_cost)
        return result
    except Exception:
        return {}


async def fetch_price_and_position(
    session: AsyncSession,
    user_id: str,
    tickers: list[str],
) -> tuple[dict[str, float], PositionMap]:
    """Sequential fetch: price_map roi position_map.

    AsyncSession khong ho tro concurrent operations — chay
    build_price_map (network) va build_position_map (DB) song song bang
    asyncio.gather gay InvalidRequestError khi session dang provisioning
    connection tu query truoc cua caller. Hai await chay tuan tu; do tre
    khong dang ke vi build_price_map la network I/O, khong phai DB query.
    """
    price_map = await build_price_map(tickers)
    position_map = await build_position_map(session, user_id)
    return price_map, position_map


async def fetch_market_context_and_position(
    session: AsyncSession,
    user_id: str,
    tickers: list[str],
) -> tuple[MarketContextMap, dict[str, float], PositionMap]:
    """Wave U2a: (context_map, price_map derive tu context, position_map).

    Neu context fetch that bai (tra {}), fallback build_price_map de P&L
    van co gia — dashboard khong mat gia chi vi thieu OHLCV.
    Sequential nhu fetch_price_and_position (AsyncSession khong concurrent).
    """
    context_map = await build_market_context_map(tickers)
    price_map = price_map_from_context(context_map)
    if not price_map and tickers:
        price_map = await build_price_map(tickers)
    position_map = await build_position_map(session, user_id)
    return context_map, price_map, position_map


async def list_thesis_tickers(
    session: AsyncSession,
    user_id: str,
    status: str | None = "active",
    ticker: str | None = None,
    limit: int | None = None,
) -> list[str]:
    """Danh sach ticker distinct cua thesis theo filter — query nhe.

    Dung de xac dinh tickers can fetch gia truoc khi chay projection day du.
    ``limit`` gioi han so *thesis* xet (khop voi limit cua get_theses_list)
    de tap ticker khong rong hon tap du lieu se tra ve.
    """
    from src.thesis.models import Thesis, ThesisStatus

    filters = [Thesis.user_id == user_id]
    if status and status != "all":
        with contextlib.suppress(ValueError):
            filters.append(Thesis.status == ThesisStatus(status))
    if ticker:
        filters.append(Thesis.ticker == ticker.upper())

    if limit is not None:
        # Cung tap thesis voi get_theses_list (order_by updated_at desc, limit).
        subq = (
            select(Thesis.ticker)
            .where(*filters)
            .order_by(Thesis.updated_at.desc())
            .limit(limit)
            .subquery()
        )
        stmt = select(subq.c.ticker).distinct()
    else:
        stmt = select(Thesis.ticker).where(*filters).distinct()

    rows = (await session.execute(stmt)).scalars().all()
    return [t for t in rows if t]


async def resolve_thesis_ticker(session: AsyncSession, thesis_id: int) -> str | None:
    """Resolve ticker cho thesis_id. None neu thesis khong ton tai."""
    from src.thesis.models import Thesis

    result = await session.execute(select(Thesis.ticker).where(Thesis.id == thesis_id))
    return result.scalar_one_or_none()
