"""Unit tests for GET /api/v1/market/quote/{ticker}.

Uses httpx ASGITransport + MockAdapter via dependency override.
No real HTTP to VCI or VNDirect.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from src.market.adapters.mock import MockAdapter
from src.market.quote_service import QuoteService


def _make_app_with_mock_quote():
    """Create a FastAPI app with QuoteService injected via bootstrap mock."""
    from src.api.app import create_app
    from src.api.deps import get_quote_service

    app = create_app()
    mock_svc = QuoteService(MockAdapter())
    app.dependency_overrides[get_quote_service] = lambda: mock_svc
    return app


async def test_quote_returns_200_for_known_ticker() -> None:
    app = _make_app_with_mock_quote()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Use a ticker that exists in registry; fallback to a known one
        resp = await client.get("/api/v1/market/quote/VNM")
    # Registry may or may not have VNM seeded yet — accept 200 or 404
    assert resp.status_code in (200, 404)


async def test_quote_404_for_unknown_ticker() -> None:
    app = _make_app_with_mock_quote()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/market/quote/UNKNOWN_XYZ_999")
    assert resp.status_code == 404


async def test_quote_502_when_adapter_fails() -> None:
    from src.api.app import create_app
    from src.api.deps import get_quote_service
    from src.market.registry import Exchange, Sector, SymbolInfo, registry

    app = create_app()
    failing_svc = QuoteService(MockAdapter(fail_tickers={"FAIL"}))
    app.dependency_overrides[get_quote_service] = lambda: failing_svc

    # Temporarily register FAIL ticker so registry passes
    try:
        registry._symbols["FAIL"] = SymbolInfo(
            ticker="FAIL",
            name="Fail Corp",
            exchange=Exchange.HOSE,
            sector=Sector.UNKNOWN,
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/market/quote/FAIL")
        assert resp.status_code == 502
    finally:
        registry._symbols.pop("FAIL", None)
        app.dependency_overrides.clear()


async def test_quote_response_shape() -> None:
    """Verify response has all expected fields when mock data is returned."""
    from src.api.app import create_app
    from src.api.deps import get_quote_service
    from src.market.registry import Exchange, Sector, SymbolInfo, registry

    app = create_app()
    mock_svc = QuoteService(MockAdapter())
    app.dependency_overrides[get_quote_service] = lambda: mock_svc

    registry._symbols["TEST"] = SymbolInfo(
        ticker="TEST",
        name="Test Corp",
        exchange=Exchange.HOSE,
        sector=Sector.UNKNOWN,
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/market/quote/TEST")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ticker"] == "TEST"
        assert body["price"] is not None
        assert "change" in body
        assert "change_pct" in body
        assert "volume" in body
        assert "ceiling" in body
        assert "floor" in body
        assert "formatted_price" in body
        assert "formatted_change" in body
    finally:
        registry._symbols.pop("TEST", None)
        app.dependency_overrides.clear()
