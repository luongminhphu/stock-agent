"""Integration tests for /api/v1/thesis/* routes.

The AI ReviewService is patched with AsyncMock to avoid real Perplexity calls.
We test:
  - Auth guards
  - 404 when thesis not found
  - 201 + correct shape when review succeeds (mocked)
  - list_reviews and latest_review endpoints
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest


def _make_mock_review(thesis_id: int = 1) -> MagicMock:
    """Build a fake ORM-like review object for mocking."""
    r = MagicMock()
    r.id = 1
    r.thesis_id = thesis_id
    r.verdict = "HOLD"
    r.confidence = 0.72
    r.reasoning = "Solid fundamentals, watch iron ore price."
    r.risk_signals = '["iron ore price drop", "credit tightening"]'
    r.next_watch_items = '["Q2 earnings", "steel export volume"]'
    r.reviewed_at = datetime(2026, 4, 18, 10, 0, tzinfo=timezone.utc)
    r.reviewed_price = 27500.0
    return r


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_review_requires_auth(client):
    r = await client.post("/api/v1/thesis/1/review")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_reviews_requires_auth(client):
    r = await client.get("/api/v1/thesis/1/reviews")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /thesis/{thesis_id}/review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_review_thesis_not_found(bootstrapped_client):
    """404 when thesis does not exist."""
    from src.thesis.service import ThesisNotFoundError

    with patch(
        "src.thesis.review_service.ReviewService.review_thesis",
        new_callable=AsyncMock,
        side_effect=ThesisNotFoundError("Thesis 999 not found"),
    ):
        r = await bootstrapped_client.post("/api/v1/thesis/999/review")
    assert r.status_code == 404
    assert "999" in r.json()["detail"]


@pytest.mark.asyncio
async def test_trigger_review_success(bootstrapped_client):
    """Successful review returns 201 with correct shape."""
    mock_review = _make_mock_review(thesis_id=1)
    with patch(
        "src.thesis.review_service.ReviewService.review_thesis",
        new_callable=AsyncMock,
        return_value=mock_review,
    ):
        r = await bootstrapped_client.post("/api/v1/thesis/1/review")

    assert r.status_code == 201
    body = r.json()
    assert body["thesis_id"] == 1
    assert body["verdict"] == "HOLD"
    assert body["confidence"] == 0.72
    assert isinstance(body["risk_signals"], list)
    assert "iron ore price drop" in body["risk_signals"]
    assert isinstance(body["next_watch_items"], list)


@pytest.mark.asyncio
async def test_trigger_review_conflict_409(bootstrapped_client):
    """ReviewNotAllowedError maps to 409."""
    from src.thesis.review_service import ReviewNotAllowedError

    with patch(
        "src.thesis.review_service.ReviewService.review_thesis",
        new_callable=AsyncMock,
        side_effect=ReviewNotAllowedError("Review cooldown active"),
    ):
        r = await bootstrapped_client.post("/api/v1/thesis/1/review")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# GET /thesis/{thesis_id}/reviews
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_reviews_returns_list(bootstrapped_client):
    reviews = [_make_mock_review(1), _make_mock_review(1)]
    reviews[1].id = 2

    with patch(
        "src.thesis.review_service.ReviewService.list_reviews",
        new_callable=AsyncMock,
        return_value=reviews,
    ):
        r = await bootstrapped_client.get("/api/v1/thesis/1/reviews")

    assert r.status_code == 200
    body = r.json()
    assert body["thesis_id"] == 1
    assert body["total"] == 2
    assert len(body["reviews"]) == 2


@pytest.mark.asyncio
async def test_list_reviews_empty_thesis_not_found(bootstrapped_client):
    from src.thesis.service import ThesisNotFoundError

    with patch(
        "src.thesis.review_service.ReviewService.list_reviews",
        new_callable=AsyncMock,
        side_effect=ThesisNotFoundError("Thesis 42 not found"),
    ):
        r = await bootstrapped_client.get("/api/v1/thesis/42/reviews")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /thesis/{thesis_id}/reviews/latest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latest_review_returns_first(bootstrapped_client):
    mock_review = _make_mock_review(1)
    with patch(
        "src.thesis.review_service.ReviewService.list_reviews",
        new_callable=AsyncMock,
        return_value=[mock_review],
    ):
        r = await bootstrapped_client.get("/api/v1/thesis/1/reviews/latest")

    assert r.status_code == 200
    assert r.json()["verdict"] == "HOLD"


@pytest.mark.asyncio
async def test_latest_review_no_reviews_404(bootstrapped_client):
    with patch(
        "src.thesis.review_service.ReviewService.list_reviews",
        new_callable=AsyncMock,
        return_value=[],
    ):
        r = await bootstrapped_client.get("/api/v1/thesis/1/reviews/latest")
    assert r.status_code == 404
