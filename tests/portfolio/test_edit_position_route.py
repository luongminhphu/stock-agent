"""Wave 7.3 — PUT /portfolio/positions/{ticker} phải commit edit kể cả khi
snapshot refresh fail.

Trước fix: refresh chạy TRƯỚC commit trên shared session → lỗi upsert
snapshot poison transaction → commit edit fail → user mất edit.
Sau fix: commit trước, refresh sau trên isolated session, never-raises.

Đặt ở tests/portfolio/ thay vì tests/api/ vì tests/api/conftest.py đang
hỏng sẵn trên main (import bootstrap bị shadow bởi src/platform/__init__).
Test tự dựng app + dependency_overrides, không cần bootstrap singletons.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import src.api.routes.portfolio as portfolio_route
from src.api.app import create_app
from src.api.deps import get_current_user_id, get_db, get_quote_service


def _fake_position(ticker: str = "HCM") -> SimpleNamespace:
    return SimpleNamespace(
        id=1, ticker=ticker, qty=57_500.0, avg_cost=14_900.0,
        thesis_id=None, closed_at=None,
    )


class _FakeQuoteSvc:
    """Thỏa QuoteServiceProtocol (isinstance check trong EodSnapshotService)."""

    async def get_quote(self, ticker: str) -> object:
        return SimpleNamespace(price=25_000.0)


async def _override_db():
    yield AsyncMock()


@pytest.mark.asyncio
async def test_edit_returns_200_when_snapshot_refresh_fails():
    """Snapshot refresh nổ (quote hang / DB error) → edit vẫn 200."""
    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user_id] = lambda: "user-test-001"
    app.dependency_overrides[get_quote_service] = lambda: object()

    with (
        patch(
            "src.portfolio.service.PortfolioService.edit_position",
            new_callable=AsyncMock,
            return_value=_fake_position(),
        ),
        # Route gọi get_quote_service() trực tiếp (không qua Depends) →
        # patch tại module route, tránh chạm bootstrap singleton.
        patch.object(
            portfolio_route, "get_quote_service", return_value=_FakeQuoteSvc(),
        ),
        patch(
            "src.portfolio.eod_snapshot_service.EodSnapshotService.refresh_after_trade",
            new_callable=AsyncMock,
            side_effect=RuntimeError("snapshot db down"),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            r = await client.put(
                "/api/v1/portfolio/positions/HCM",
                json={"qty": 57_500, "avg_cost": 14_900},
            )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticker"] == "HCM"
    assert body["qty"] == 57_500.0
    assert body["avg_cost"] == 14_900.0


@pytest.mark.asyncio
async def test_refresh_after_edit_never_raises():
    """Helper phải nuốt mọi lỗi — kể cả khi chính session factory hỏng."""
    with patch(
        "src.api.routes.portfolio.AsyncSessionLocal",
        side_effect=RuntimeError("session factory broken"),
    ):
        # Không được raise
        await portfolio_route._refresh_snapshot_after_commit(_FakeQuoteSvc(), "u1", "HCM")


@pytest.mark.asyncio
async def test_refresh_after_edit_commits_isolated_session():
    """Happy path: refresh chạy trên session riêng và commit session đó."""
    fake_session = AsyncMock()

    class _FakeSessionCtx:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *args):
            return False

    refresh = AsyncMock()
    with (
        patch(
            "src.api.routes.portfolio.AsyncSessionLocal",
            return_value=_FakeSessionCtx(),
        ),
        patch(
            "src.portfolio.eod_snapshot_service.EodSnapshotService.refresh_after_trade",
            refresh,
        ),
    ):
        await portfolio_route._refresh_snapshot_after_commit(_FakeQuoteSvc(), "u1", "HCM")

    refresh.assert_awaited_once()
    assert refresh.await_args.args[1] == "HCM"
    fake_session.commit.assert_awaited_once()
