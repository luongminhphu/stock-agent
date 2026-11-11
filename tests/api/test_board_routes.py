"""GET /api/v1/board — adapter mỏng trên readmodel.position_board."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_board_empty_user(app, bootstrapped_client) -> None:  # type: ignore[no-untyped-def]
    from src.api.deps import get_current_user_id

    app.dependency_overrides[get_current_user_id] = lambda: "nobody-board"
    try:
        r = await bootstrapped_client.get("/api/v1/board")
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == []
    assert body["summary"]["holdings"] == 0
