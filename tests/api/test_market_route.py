"""Unit tests for GET /api/v1/market/quote/{ticker}.

Uses httpx ASGITransport + MockAdapter via dependency override.
No real HTTP to VCI or VNDirect.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from src.market.adapters.mock import MockAdapter
from src.market.quote_service import QuoteService, TradingHoursGuard

# Test chạy ngoài giờ giao dịch → guard phải bypass (như MARKET_FETCH_ALWAYS=true).
_ALWAYS_OPEN = TradingHoursGuard(always=True)


def _mock_quote_service(**adapter_kwargs) -> QuoteService:
    return QuoteService(MockAdapter(**adapter_kwargs), guard=_ALWAYS_OPEN)


def _make_app_with_mock_quote():
    """Create a FastAPI app with QuoteService injected via dependency override."""
    from src.api.app import create_app
    from src.api.deps import get_quote_service

    app = create_app()
    mock_svc = _mock_quote_service()
    app.dependency_overrides[get_quote_service] = lambda: mock_svc
    return app


async def test_quote_returns_200_for_known_ticker() -> None:
    app = _make_app_with_mock_quote()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Use a ticker that exists in registry; fallback to a known one
        resp = await client.get("/api/v1/market/quote/VNM")
    # VNM nằm trong _STATIC_SEED của registry → luôn 200 với MockAdapter.
    assert resp.status_code == 200
    assert resp.json()["ticker"] == "VNM"


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
    failing_svc = _mock_quote_service(fail_tickers={"FAIL"})
    app.dependency_overrides[get_quote_service] = lambda: failing_svc

    # Temporarily register FAIL ticker so registry passes
    try:
        registry._cache["FAIL"] = SymbolInfo(
            ticker="FAIL",
            name="Fail Corp",
            exchange=Exchange.HOSE,
            sector=Sector.OTHER,
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/market/quote/FAIL")
        assert resp.status_code == 502
    finally:
        registry._cache.pop("FAIL", None)
        app.dependency_overrides.clear()


async def test_quote_response_shape() -> None:
    """Verify response has all expected fields when mock data is returned."""
    from src.api.app import create_app
    from src.api.deps import get_quote_service
    from src.market.registry import Exchange, Sector, SymbolInfo, registry

    app = create_app()
    mock_svc = _mock_quote_service()
    app.dependency_overrides[get_quote_service] = lambda: mock_svc

    registry._cache["TEST"] = SymbolInfo(
        ticker="TEST",
        name="Test Corp",
        exchange=Exchange.HOSE,
        sector=Sector.OTHER,
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
        registry._cache.pop("TEST", None)
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Wave U2a — GET /market/context/{ticker}
# ---------------------------------------------------------------------------


def _make_app_with_mock_context(ctx_or_exc):
    from unittest.mock import AsyncMock

    from src.api.app import create_app
    from src.api.deps import get_ticker_context_service

    app = create_app()
    svc = AsyncMock()
    if isinstance(ctx_or_exc, Exception):
        svc.get.side_effect = ctx_or_exc
    else:
        svc.get.return_value = ctx_or_exc
    app.dependency_overrides[get_ticker_context_service] = lambda: svc
    return app


def _sample_context():
    from datetime import UTC, datetime

    from src.market.quote_service import Quote
    from src.market.ticker_context import TickerContext

    now = datetime(2026, 9, 18, 3, 0, tzinfo=UTC)
    q = Quote(
        ticker="VNM",
        price=70_000.0,
        change=500.0,
        change_pct=0.72,
        volume=1_200_000,
        value=8.4e10,
        open=69_500.0,
        high=70_500.0,
        low=69_000.0,
        ref_price=69_500.0,
        ceiling=74_350.0,
        floor=64_650.0,
        timestamp=now,
    )
    return TickerContext(
        ticker="VNM",
        as_of=now,
        quote=q,
        ma20=68_000.0,
        ma50=66_000.0,
        rsi14=61.234,
        atr14=1_500.0,
        vol_ratio_20=1.456,
        hi_52w=80_000.0,
        lo_52w=55_000.0,
        bars=120,
        trend_state="UPTREND",
        source_quality="live",
    )


async def test_context_returns_indicators_and_quality() -> None:
    app = _make_app_with_mock_context(_sample_context())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/market/context/vnm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "VNM"
    assert body["price"] == 70_000.0
    assert body["change_pct"] == 0.72
    assert body["ma20"] == 68_000.0
    assert body["rsi14"] == 61.2
    assert body["vol_ratio_20"] == 1.46
    assert body["dist_to_ma20_pct"] == 2.94
    assert body["dist_to_hi_52w_pct"] == -12.5
    assert body["trend_state"] == "UPTREND"
    assert body["source_quality"] == "live"
    assert body["is_ceiling"] is False
    assert body["formatted_price"]


async def test_context_404_for_unknown_ticker() -> None:
    app = _make_app_with_mock_context(_sample_context())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/market/context/UNKNOWN_XYZ_999")
    assert resp.status_code == 404


async def test_context_502_when_service_fails() -> None:
    app = _make_app_with_mock_context(LookupError("No quote available for VNM"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/market/context/VNM")
    assert resp.status_code == 502
