"""Shared fixtures for API integration tests.

Strategy:
- Use httpx.AsyncClient + ASGITransport (no real server)
- bootstrap() called manually per test that needs live singletons
- Services that need DB use SQLite in-memory (already configured via conftest env)
- Services that need AI (ReviewService) are patched with AsyncMock
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.platform import bootstrap as _bs


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset bootstrap singletons before/after every test."""
    _bs.reset_singletons()
    yield
    _bs.reset_singletons()


@pytest.fixture()
def app():
    """FastAPI app without lifespan (bootstrap controlled per test)."""
    from src.api.app import create_app

    return create_app()


@pytest.fixture()
async def client(app):
    """Unauthenticated async client."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture()
async def auth_client(app):
    """Client with X-User-Id header pre-set."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "user-test-001"},
    ) as ac:
        yield ac


@pytest.fixture()
async def bootstrapped_client(app):
    """Client where bootstrap() has already run (singletons ready)."""
    await _bs.bootstrap()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-User-Id": "user-test-001"},
    ) as ac:
        yield ac
