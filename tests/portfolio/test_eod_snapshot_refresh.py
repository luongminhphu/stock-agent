"""refresh_today_snapshot must CREATE today's row when missing.

Dashboard reads get_latest_snapshots() (max date per ticker). If we only
update an existing today-row, edits before the 15:20 EOD job leave
yesterday's qty/avg on screen.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.portfolio.eod_snapshot_service import EodSnapshotService


class _FakeQuoteSvc:
    async def get_quote(self, ticker: str) -> object:
        return SimpleNamespace(price=25_000.0)


def _make_svc() -> tuple[EodSnapshotService, AsyncMock]:
    session = AsyncMock()
    svc = EodSnapshotService(session=session, quote_service=_FakeQuoteSvc())
    svc._upsert_snapshot = AsyncMock()
    return svc, session


def _pos(**kwargs: object) -> SimpleNamespace:
    defaults = dict(user_id="u1", ticker="FPT", qty=1500.0, avg_cost=80_000.0, thesis_id=None)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_refresh_creates_today_row_when_missing():
    svc, session = _make_svc()
    today_result = MagicMock()
    today_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=today_result)

    ok = await svc.refresh_today_snapshot(_pos())

    assert ok is True
    svc._upsert_snapshot.assert_awaited_once()
    pos, close_price, _date = svc._upsert_snapshot.await_args.args
    assert pos.ticker == "FPT"
    assert pos.qty == 1500.0
    assert close_price == 25_000.0


@pytest.mark.asyncio
async def test_refresh_still_upserts_when_today_row_exists():
    svc, session = _make_svc()
    today_result = MagicMock()
    today_result.scalar_one_or_none.return_value = SimpleNamespace(close_price=24_100.0)
    session.execute = AsyncMock(return_value=today_result)

    ok = await svc.refresh_today_snapshot(_pos(qty=2000.0, avg_cost=70_000.0))

    assert ok is True
    svc._upsert_snapshot.assert_awaited_once()
    pos, close_price, _date = svc._upsert_snapshot.await_args.args
    assert pos.qty == 2000.0
    assert close_price == 25_000.0  # quote wins over existing close


@pytest.mark.asyncio
async def test_refresh_falls_back_to_latest_close_when_quote_fails():
    svc, session = _make_svc()
    today_result = MagicMock()
    today_result.scalar_one_or_none.return_value = None
    latest_result = MagicMock()
    latest_result.scalar_one_or_none.return_value = 24_500.0
    session.execute = AsyncMock(side_effect=[today_result, latest_result])
    svc._fetch_close_price = AsyncMock(side_effect=RuntimeError("quote down"))

    ok = await svc.refresh_today_snapshot(_pos())

    assert ok is True
    _, close_price, _ = svc._upsert_snapshot.await_args.args
    assert close_price == 24_500.0


@pytest.mark.asyncio
async def test_refresh_falls_back_to_avg_cost_when_no_snapshot_and_no_quote():
    svc, session = _make_svc()
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=empty)
    svc._fetch_close_price = AsyncMock(side_effect=RuntimeError("quote down"))

    ok = await svc.refresh_today_snapshot(_pos(avg_cost=81_200.0))

    assert ok is True
    _, close_price, _ = svc._upsert_snapshot.await_args.args
    assert close_price == 81_200.0
