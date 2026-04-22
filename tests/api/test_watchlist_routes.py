"""Integration tests for /api/v1/watchlist routes.

Owner: api + watchlist segments.
Uses SQLite in-memory DB (ENVIRONMENT=test).
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_watchlist_requires_auth(client):
    """Without X-User-Id header, returns 401."""
    r = await client.get("/api/v1/watchlist")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_add_watchlist_requires_auth(client):
    r = await client.post("/api/v1/watchlist", json={"ticker": "HPG"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# CRUD happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_empty_watchlist(auth_client):
    """Fresh user has empty watchlist."""
    r = await auth_client.get("/api/v1/watchlist")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_add_and_list_watchlist(auth_client):
    """Add item then list returns it."""
    add = await auth_client.post("/api/v1/watchlist", json={"ticker": "HPG", "note": "steel play"})
    assert add.status_code == 201
    item = add.json()
    assert item["ticker"] == "HPG"
    assert item["note"] == "steel play"
    assert "id" in item

    lst = await auth_client.get("/api/v1/watchlist")
    assert lst.status_code == 200
    assert lst.json()["total"] == 1
    assert lst.json()["items"][0]["ticker"] == "HPG"


@pytest.mark.asyncio
async def test_add_duplicate_returns_409(auth_client):
    """Adding same ticker twice returns 409 Conflict."""
    await auth_client.post("/api/v1/watchlist", json={"ticker": "VCB"})
    r = await auth_client.post("/api/v1/watchlist", json={"ticker": "VCB"})
    assert r.status_code == 409
    assert "VCB" in r.json()["detail"]


@pytest.mark.asyncio
async def test_add_ticker_normalised_to_uppercase(auth_client):
    """Ticker is normalised to uppercase regardless of input."""
    r = await auth_client.post("/api/v1/watchlist", json={"ticker": "fpt"})
    assert r.status_code == 201
    assert r.json()["ticker"] == "FPT"


@pytest.mark.asyncio
async def test_remove_watchlist_item(auth_client):
    """Add then remove — watchlist becomes empty again."""
    await auth_client.post("/api/v1/watchlist", json={"ticker": "MSN"})
    delete = await auth_client.delete("/api/v1/watchlist/MSN")
    assert delete.status_code == 204

    lst = await auth_client.get("/api/v1/watchlist")
    assert lst.json()["total"] == 0


@pytest.mark.asyncio
async def test_remove_nonexistent_returns_404(auth_client):
    r = await auth_client.delete("/api/v1/watchlist/NOTEXIST")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_multiple_items_independent_per_user(app):
    """Two users have independent watchlists."""
    from httpx import ASGITransport, AsyncClient

    async with (
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-User-Id": "user-A"},
        ) as client_a,
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-User-Id": "user-B"},
        ) as client_b,
    ):
        await client_a.post("/api/v1/watchlist", json={"ticker": "HPG"})
        await client_b.post("/api/v1/watchlist", json={"ticker": "VCB"})

        lst_a = (await client_a.get("/api/v1/watchlist")).json()
        lst_b = (await client_b.get("/api/v1/watchlist")).json()

        assert lst_a["total"] == 1
        assert lst_a["items"][0]["ticker"] == "HPG"
        assert lst_b["total"] == 1
        assert lst_b["items"][0]["ticker"] == "VCB"
