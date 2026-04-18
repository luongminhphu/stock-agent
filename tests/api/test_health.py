"""Integration smoke test — FastAPI app boots and /health responds."""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_health_ok():
    """App creates successfully and /health returns 200."""
    import os
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("MOCK_MARKET", "true")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    os.environ.setdefault("DISCORD_TOKEN", "test-token")
    os.environ.setdefault("PERPLEXITY_API_KEY", "test-key")

    # Import after env vars are set
    from src.api.app import create_app
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
