"""Unit tests for health routes.

Uses httpx.AsyncClient with ASGITransport — no real DB required.
Readiness probe DB check is mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport

from src.api.app import create_app


@pytest.fixture()
def app():
    return create_app()


async def test_liveness_returns_200(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"


async def test_liveness_contains_env(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert "env" in resp.json()


async def test_readiness_db_ok(app) -> None:
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=None)

    async def override_get_db():
        yield mock_session

    from src.api.deps import get_db
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health/ready")

    app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["db"] is True


async def test_readiness_db_fail(app) -> None:
    async def broken_db():
        mock = AsyncMock()
        mock.execute = AsyncMock(side_effect=Exception("DB down"))
        yield mock

    from src.api.deps import get_db
    app.dependency_overrides[get_db] = broken_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health/ready")

    app.dependency_overrides.clear()
    assert resp.status_code == 200  # still 200 — caller decides traffic routing
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["db"] is False


async def test_get_quote_requires_known_ticker(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/market/quote/UNKNOWN_XYZ")
    assert resp.status_code == 404


async def test_watchlist_requires_user_id_header(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/watchlist")
    assert resp.status_code == 401
