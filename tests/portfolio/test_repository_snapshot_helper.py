"""Boundary fix B2: PortfolioRepository.has_snapshot_for_date thay truy vấn ORM trong bot.scheduler."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.platform.db import AsyncSessionLocal
from src.portfolio.models import PositionDailySnapshot
from src.portfolio.repository import PortfolioRepository


@pytest.mark.anyio
async def test_has_snapshot_for_date() -> None:
    d = date(2026, 9, 18)
    async with AsyncSessionLocal() as s:
        s.add(
            PositionDailySnapshot(
                user_id="u1",
                ticker="HPG",
                snapshot_date=d,
                qty=100,
                avg_cost=25.0,
                close_price=26.0,
                cost_basis=2500.0,
                market_value=2600.0,
                unrealized_pnl=100.0,
                unrealized_pct=0.04,
                created_at=datetime.now(UTC),
            )
        )
        await s.commit()
        repo = PortfolioRepository(s)
        assert await repo.has_snapshot_for_date("u1", d) is True
        assert await repo.has_snapshot_for_date("u1", date(2026, 9, 19)) is False
        assert await repo.has_snapshot_for_date("u2", d) is False
