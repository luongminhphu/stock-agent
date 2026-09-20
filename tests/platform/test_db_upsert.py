"""Wave E1: platform.db.upsert_rows — helper upsert chung cho các snapshot store."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from src.platform.db import AsyncSessionLocal, upsert_rows
from src.readmodel.models import DailyAgenda, MarketQuoteCache, TrendSnapshot


@pytest.mark.anyio
async def test_upsert_single_row_insert_then_update() -> None:
    now = datetime.now(UTC)
    ok = await upsert_rows(
        AsyncSessionLocal,
        TrendSnapshot,
        {"symbol": "HPG", "bundle_json": json.dumps({"v": 1}), "saved_at": now},
        conflict_columns=["symbol"],
    )
    assert ok is True
    ok = await upsert_rows(
        AsyncSessionLocal,
        TrendSnapshot,
        {"symbol": "HPG", "bundle_json": json.dumps({"v": 2}), "saved_at": now},
        conflict_columns=["symbol"],
    )
    assert ok is True
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(TrendSnapshot))).scalars().all()
    assert len(rows) == 1 and json.loads(rows[0].bundle_json) == {"v": 2}


@pytest.mark.anyio
async def test_upsert_batch_sorted_and_partial_update_columns() -> None:
    now = datetime.now(UTC)
    base = {
        "change": 0.0,
        "change_pct": 0.0,
        "volume": 0,
        "value": 0.0,
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "ref_price": 1.0,
        "ceiling": 1.0,
        "floor": 1.0,
        "quote_ts": now,
        "saved_at": now,
    }
    rows = [
        {"ticker": "VNM", "price": 70.0, **base},
        {"ticker": "HPG", "price": 23.0, **base},
    ]
    assert await upsert_rows(AsyncSessionLocal, MarketQuoteCache, rows, conflict_columns=["ticker"])
    # update chỉ price; volume giữ nguyên dù row mới có volume khác
    rows2 = [{"ticker": "HPG", "price": 24.0, **{**base, "volume": 999}}]
    assert await upsert_rows(
        AsyncSessionLocal,
        MarketQuoteCache,
        rows2,
        conflict_columns=["ticker"],
        update_columns=["price", "saved_at"],
    )
    async with AsyncSessionLocal() as s:
        got = {r.ticker: r for r in (await s.execute(select(MarketQuoteCache))).scalars().all()}
    assert set(got) == {"HPG", "VNM"}
    assert got["HPG"].price == 24.0 and got["HPG"].volume == 0


@pytest.mark.anyio
async def test_upsert_by_named_constraint_works_on_sqlite() -> None:
    today = date.today()
    row = {
        "user_id": "u1",
        "agenda_date": today,
        "summary": "a",
        "buckets_json": None,
        "created_at": datetime.now(UTC),
    }
    assert await upsert_rows(
        AsyncSessionLocal, DailyAgenda, row, constraint="uq_daily_agendas_user_date"
    )
    assert await upsert_rows(
        AsyncSessionLocal,
        DailyAgenda,
        {**row, "summary": "b"},
        constraint="uq_daily_agendas_user_date",
    )
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(DailyAgenda))).scalars().all()
    assert len(rows) == 1 and rows[0].summary == "b"


@pytest.mark.anyio
async def test_upsert_never_raises_and_skips_empty() -> None:
    assert (
        await upsert_rows(None, TrendSnapshot, {"symbol": "X"}, conflict_columns=["symbol"])
        is False
    )
    assert (
        await upsert_rows(AsyncSessionLocal, TrendSnapshot, [], conflict_columns=["symbol"])
        is False
    )
    # cột không tồn tại → lỗi DB → nuốt, trả False
    assert (
        await upsert_rows(
            AsyncSessionLocal,
            TrendSnapshot,
            {"symbol": "X", "nope": 1},
            conflict_columns=["symbol"],
        )
        is False
    )
    with pytest.raises(ValueError):
        await upsert_rows(AsyncSessionLocal, TrendSnapshot, {"symbol": "X"})
