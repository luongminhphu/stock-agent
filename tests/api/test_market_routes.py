"""Integration tests for GET /api/v1/market/* routes.

Owner: api + market segments.
Uses MockAdapter (ENVIRONMENT=test) — no real HTTP calls.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# GET /api/v1/market/symbols/{ticker}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_symbol_known_ticker(client):
    """Known ticker returns 200 with correct fields."""
    r = await client.get("/api/v1/market/symbols/HPG")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "HPG"
    assert body["name"] == "Hoa Phat Group"
    assert body["exchange"] == "HOSE"
    assert body["sector"] == "Materials"


@pytest.mark.asyncio
async def test_get_symbol_case_insensitive(client):
    """Ticker lookup is case-insensitive."""
    r = await client.get("/api/v1/market/symbols/vcb")
    assert r.status_code == 200
    assert r.json()["ticker"] == "VCB"


@pytest.mark.asyncio
async def test_get_symbol_unknown_ticker_404(client):
    r = await client.get("/api/v1/market/symbols/UNKNOWN")
    assert r.status_code == 404
    assert "UNKNOWN" in r.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/v1/market/quote/{ticker}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_quote_returns_price(bootstrapped_client):
    """After bootstrap, quote returns price data from MockAdapter."""
    r = await bootstrapped_client.get("/api/v1/market/quote/HPG")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "HPG"
    assert body["name"] == "Hoa Phat Group"
    assert isinstance(body["price"], float)
    assert body["price"] > 0
    assert isinstance(body["change_pct"], float)
    assert body["volume"] is not None


@pytest.mark.asyncio
async def test_get_quote_mock_deterministic(bootstrapped_client):
    """MockAdapter returns same price on repeated calls for same ticker."""
    r1 = await bootstrapped_client.get("/api/v1/market/quote/VCB")
    r2 = await bootstrapped_client.get("/api/v1/market/quote/VCB")
    assert r1.json()["price"] == r2.json()["price"]


@pytest.mark.asyncio
async def test_get_quote_unknown_ticker_404(bootstrapped_client):
    r = await bootstrapped_client.get("/api/v1/market/quote/XXXXXX")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_quote_before_bootstrap_500(client):
    """Without bootstrap, get_quote_service() raises RuntimeError → 500."""
    r = await client.get("/api/v1/market/quote/HPG")
    assert r.status_code in (500, 502, 503)  # unhandled RuntimeError


@pytest.mark.asyncio
async def test_get_quote_different_tickers_different_prices(bootstrapped_client):
    """Each ticker gets its own deterministic price."""
    r_hpg = await bootstrapped_client.get("/api/v1/market/quote/HPG")
    r_vcb = await bootstrapped_client.get("/api/v1/market/quote/VCB")
    assert r_hpg.json()["price"] != r_vcb.json()["price"]
