"""Integration tests for /api/v1/readmodel/* routes.

Chạy trên SQLite in-memory thật (không mock service) để kiểm tra contract
route ↔ readmodel: shape phân trang {items,total}, 404, 422, alias field
của timeline (event_type / occurred_at).

Skipped: /dashboard/stats (dùng func.timezone — Postgres-only).
"""

from __future__ import annotations

import pytest

from src.thesis.dtos import CreateThesisInput
from src.thesis.service import ThesisService

USER = "user-test-001"


async def _create_thesis(session, ticker: str = "HPG") -> int:
    thesis = await ThesisService(session).create(
        CreateThesisInput(
            user_id=USER,
            ticker=ticker,
            title=f"{ticker} recovery",
            summary="Test thesis",
            assumptions=["Demand holds"],
        )
    )
    await session.commit()
    return thesis.id


# ---------------------------------------------------------------------------
# GET /readmodel/dashboard/{user_id}/theses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theses_list_empty(bootstrapped_client):
    r = await bootstrapped_client.get(f"/api/v1/readmodel/dashboard/{USER}/theses")
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_theses_list_single_user_alias_url(bootstrapped_client):
    """URL không có user_id → resolve về owner_user_id, cùng kết quả."""
    r = await bootstrapped_client.get("/api/v1/readmodel/dashboard/theses")
    assert r.status_code == 200
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_theses_list_returns_created_thesis(bootstrapped_client, session):
    thesis_id = await _create_thesis(session)
    r = await bootstrapped_client.get(
        f"/api/v1/readmodel/dashboard/{USER}/theses", params={"enrich_prices": "false"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == thesis_id
    assert body["items"][0]["ticker"] == "HPG"


@pytest.mark.asyncio
async def test_thesis_detail_404(bootstrapped_client):
    r = await bootstrapped_client.get(f"/api/v1/readmodel/dashboard/{USER}/theses/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_thesis_detail_returns_components(bootstrapped_client, session):
    thesis_id = await _create_thesis(session)
    r = await bootstrapped_client.get(f"/api/v1/readmodel/dashboard/{USER}/theses/{thesis_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["thesis"]["id"] == thesis_id
    assert body["thesis"]["ticker"] == "HPG"
    assert body["thesis"]["n_assumptions"] == 1
    assert [a["description"] for a in body["assumptions"]] == ["Demand holds"]
    assert body["reviews"] == []


# ---------------------------------------------------------------------------
# GET /readmodel/leaderboard/{user_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leaderboard_returns_200(bootstrapped_client):
    r = await bootstrapped_client.get(f"/api/v1/readmodel/leaderboard/{USER}")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == USER
    assert body["sort_by"] == "score"
    assert body["entries"] == []


@pytest.mark.asyncio
async def test_leaderboard_sort_by_pnl(bootstrapped_client):
    r = await bootstrapped_client.get(f"/api/v1/readmodel/leaderboard/{USER}?sort_by=pnl")
    assert r.status_code == 200
    assert r.json()["sort_by"] == "pnl"


@pytest.mark.asyncio
async def test_leaderboard_invalid_sort_by_422(bootstrapped_client):
    """Invalid sort_by value rejected at FastAPI validation layer."""
    r = await bootstrapped_client.get(f"/api/v1/readmodel/leaderboard/{USER}?sort_by=invalid")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /readmodel/thesis/{thesis_id}/timeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeline_returns_created_event(bootstrapped_client, session):
    thesis_id = await _create_thesis(session)
    r = await bootstrapped_client.get(f"/api/v1/readmodel/thesis/{thesis_id}/timeline")
    assert r.status_code == 200
    body = r.json()
    assert body["thesis_id"] == thesis_id
    assert body["ticker"] == "HPG"
    assert body["events"], "thesis creation must produce at least one timeline event"
    first = body["events"][0]
    # serialization_alias contract cho UI
    assert {"event_type", "occurred_at", "summary"} <= set(first)
    assert "kind" not in first


@pytest.mark.asyncio
async def test_timeline_not_found_404(bootstrapped_client):
    r = await bootstrapped_client.get("/api/v1/readmodel/thesis/999/timeline")
    assert r.status_code == 404
