"""Wave 7.4 — buy/sell/adjust routes: commit TRƯỚC, snapshot refresh SAU
trên isolated session (cùng pattern Wave 7.3 cho edit).

Trước fix: _refresh_snapshot_after_trade chạy trên shared session trước
khi get_db commit → lỗi snapshot (DB error dù đã catch bên trong service
vẫn poison transaction) → commit trade fail → user mất lệnh đã đúng.

Đặt ở tests/portfolio/ vì tests/api/conftest.py hỏng sẵn trên main.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user_id, get_db, get_quote_service
from src.portfolio.trade_usecase import TradeResult


class _FakeQuoteSvc:
    """Thỏa QuoteServiceProtocol (isinstance check trong EodSnapshotService)."""

    async def get_quote(self, ticker: str) -> object:
        raise RuntimeError("quote down — không được gọi tới trong test này")


def _trade_result(**overrides) -> TradeResult:
    defaults = dict(
        trade_id=10, position_id=5, ticker="HCM", trade_type="buy",
        qty=1_000.0, price=15_000.0, avg_cost=14_900.0,
        position_qty=58_500.0, realized_pnl=None, position_closed=False,
        decision_logged=False,
    )
    defaults.update(overrides)
    return TradeResult(**defaults)


def _make_app(fake_session: AsyncMock):
    app = create_app()

    async def _override_db():
        yield fake_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user_id] = lambda: "user-test-001"
    app.dependency_overrides[get_quote_service] = lambda: _FakeQuoteSvc()
    return app


@pytest.mark.asyncio
async def test_buy_returns_201_when_snapshot_refresh_fails():
    """Snapshot refresh nổ → lệnh mua vẫn 201, trade đã commit."""
    fake_session = AsyncMock()
    app = _make_app(fake_session)

    with (
        patch(
            "src.portfolio.trade_usecase.TradeUseCase.execute_buy",
            new_callable=AsyncMock,
            return_value=_trade_result(),
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
            r = await client.post(
                "/api/v1/portfolio/buy",
                json={"ticker": "HCM", "qty": 1_000, "price": 15_000},
            )

    assert r.status_code == 201, r.text
    assert r.json()["ticker"] == "HCM"
    # Route phải tự commit trade (trước đây chỉ get_db commit sau khi return)
    fake_session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_sell_full_refresh_called_with_position_closed():
    """Full sell → refresh snapshot nhận position_closed=True để xoá row
    hôm nay; lỗi refresh không ảnh hưởng response."""
    fake_session = AsyncMock()
    app = _make_app(fake_session)
    refresh = AsyncMock(side_effect=RuntimeError("snapshot db down"))

    with (
        patch(
            "src.portfolio.trade_usecase.TradeUseCase.execute_sell",
            new_callable=AsyncMock,
            return_value=_trade_result(
                trade_type="sell", realized_pnl=1_250_000.0,
                position_qty=0.0, position_closed=True,
            ),
        ),
        patch(
            "src.portfolio.eod_snapshot_service.EodSnapshotService.refresh_after_trade",
            refresh,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            r = await client.post(
                "/api/v1/portfolio/sell",
                json={"ticker": "HCM", "qty": 58_500, "price": 16_000},
            )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["position_closed"] is True
    assert body["realized_pnl"] == 1_250_000.0
    fake_session.commit.assert_awaited()
    refresh.assert_awaited_once()
    assert refresh.await_args.kwargs["position_closed"] is True


@pytest.mark.asyncio
async def test_buy_business_error_still_400_and_no_commit():
    """Lỗi domain (ValueError) → 400, KHÔNG commit, không refresh."""
    fake_session = AsyncMock()
    app = _make_app(fake_session)
    refresh = AsyncMock()

    with (
        patch(
            "src.portfolio.trade_usecase.TradeUseCase.execute_buy",
            new_callable=AsyncMock,
            side_effect=ValueError("qty phải > 0"),
        ),
        patch(
            "src.portfolio.eod_snapshot_service.EodSnapshotService.refresh_after_trade",
            refresh,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            r = await client.post(
                "/api/v1/portfolio/buy",
                json={"ticker": "HCM", "qty": 1_000, "price": 15_000},
            )

    assert r.status_code == 400, r.text
    fake_session.commit.assert_not_awaited()
    refresh.assert_not_awaited()
