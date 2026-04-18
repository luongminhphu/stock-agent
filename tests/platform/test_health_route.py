"""HTTP-level tests for /health and /ready API routes."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.platform import bootstrap as _bs


@pytest.fixture(autouse=True)
def reset_between_tests():
    _bs.reset_singletons()
    yield
    _bs.reset_singletons()


@pytest.fixture()
def app():
    """Create the FastAPI app without triggering lifespan (bootstrap controlled manually)."""
    from src.api.app import create_app
    return create_app()


@pytest.fixture()
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# /health  (liveness)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_liveness_route_200(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")  # always responds


# ---------------------------------------------------------------------------
# /ready  (readiness)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ready_route_degraded_before_bootstrap(client):
    r = await client.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"


@pytest.mark.asyncio
async def test_ready_route_200_after_bootstrap(client):
    await _bs.bootstrap()
    r = await client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
