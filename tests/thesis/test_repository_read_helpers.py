"""Boundary fix: helper đọc của ThesisRepository thay truy vấn ORM trong adapter."""

from __future__ import annotations

import pytest

from src.platform.db import AsyncSessionLocal
from src.thesis.models import Thesis, ThesisStatus
from src.thesis.repository import ThesisRepository


async def _seed() -> None:
    async with AsyncSessionLocal() as s:
        s.add_all(
            [
                Thesis(user_id="u1", ticker="HPG", title="a", status=ThesisStatus.ACTIVE),
                Thesis(user_id="u1", ticker="HPG", title="b", status=ThesisStatus.ACTIVE),
                Thesis(user_id="u1", ticker="VNM", title="c", status=ThesisStatus.WEAKENING),
                Thesis(user_id="u1", ticker="SSI", title="d", status=ThesisStatus.INVALIDATED),
                Thesis(user_id="u2", ticker="FPT", title="e", status=ThesisStatus.ACTIVE),
            ]
        )
        await s.commit()


@pytest.mark.anyio
async def test_list_active_tickers_dedup_and_status_filter() -> None:
    await _seed()
    async with AsyncSessionLocal() as s:
        repo = ThesisRepository(s)
        assert set(await repo.list_active_tickers("u1")) == {"HPG"}
        both = await repo.list_active_tickers(
            "u1", statuses=(ThesisStatus.ACTIVE, ThesisStatus.WEAKENING)
        )
        assert set(both) == {"HPG", "VNM"} and len(both) == 2


@pytest.mark.anyio
async def test_latest_active_by_ticker() -> None:
    await _seed()
    async with AsyncSessionLocal() as s:
        repo = ThesisRepository(s)
        latest = await repo.latest_active_by_ticker("hpg")
        assert latest is not None and latest.title == "b"
        assert await repo.latest_active_by_ticker("SSI") is None
