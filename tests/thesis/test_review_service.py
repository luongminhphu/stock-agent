"""Unit tests for ReviewService.

All external dependencies (agent, repo, quote_service) are AsyncMock.
No DB, no HTTP.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ai.schemas import ThesisReviewOutput
from src.thesis.models import (
    AssumptionStatus,
    CatalystStatus,
    ReviewVerdict,
    ThesisStatus,
)
from src.thesis.review_service import ReviewNotAllowedError, ReviewService
from src.thesis.service import ThesisNotFoundError

from tests.thesis.conftest import make_assumption, make_catalyst, make_review, make_thesis


def _make_output(
    verdict: str = "BULLISH",
    confidence: float = 0.80,
) -> ThesisReviewOutput:
    return ThesisReviewOutput(
        verdict=ReviewVerdict(verdict),
        confidence=confidence,
        reasoning="Strong steel demand outlook.",
        risk_signals=["iron ore price drop"],
        next_watch_items=["Q2 earnings"],
        action="Hold position",
    )


def _make_service(
    mock_repo: AsyncMock,
    mock_agent: AsyncMock,
    quote_service: object | None = None,
) -> ReviewService:
    svc = ReviewService.__new__(ReviewService)
    svc._repo = mock_repo
    svc._agent = mock_agent
    svc._quote_service = quote_service
    return svc


# ---------------------------------------------------------------------------
# review_thesis — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_thesis_happy_path(mock_repo, mock_agent):
    """Active thesis → agent called → review persisted and returned."""
    output = _make_output()
    mock_agent.review.return_value = output

    svc = _make_service(mock_repo, mock_agent)
    result = await svc.review_thesis(thesis_id=1, user_id="user-test-001")

    mock_agent.review.assert_awaited_once()
    mock_repo.save_review.assert_awaited_once()
    assert result.verdict == ReviewVerdict.BULLISH
    assert result.confidence == 0.80


@pytest.mark.asyncio
async def test_review_thesis_with_assumptions_and_catalysts(mock_repo, mock_agent):
    """Assumptions + catalysts are correctly extracted and passed to agent."""
    thesis = make_thesis(
        assumptions=[
            make_assumption("Steel demand grows", AssumptionStatus.VALID),
            make_assumption("Export quota stays", AssumptionStatus.INVALID),  # excluded
        ],
        catalysts=[
            make_catalyst("Q2 earnings", CatalystStatus.TRIGGERED),
            make_catalyst("Credit expansion", CatalystStatus.PENDING),
            make_catalyst("Old catalyst", CatalystStatus.EXPIRED),  # excluded
        ],
    )
    mock_repo.get_by_id.return_value = thesis
    mock_agent.review.return_value = _make_output()

    svc = _make_service(mock_repo, mock_agent)
    await svc.review_thesis(thesis_id=1, user_id="user-test-001")

    call_kwargs = mock_agent.review.call_args.kwargs
    # INVALID assumption excluded
    assert "Steel demand grows" in call_kwargs["assumptions"]
    assert "Export quota stays" not in call_kwargs["assumptions"]
    # Only TRIGGERED + PENDING catalysts included; EXPIRED excluded
    assert "Q2 earnings" in call_kwargs["catalysts"]
    assert "Credit expansion" in call_kwargs["catalysts"]
    assert "Old catalyst" not in call_kwargs["catalysts"]


@pytest.mark.asyncio
async def test_review_thesis_price_from_quote_service(mock_repo, mock_agent):
    """When current_price is None, QuoteService is called for live price."""
    mock_agent.review.return_value = _make_output()

    mock_qs = AsyncMock()
    mock_quote = MagicMock()
    mock_quote.price = 29500.0
    mock_qs.get_quote.return_value = mock_quote

    svc = _make_service(mock_repo, mock_agent, quote_service=mock_qs)
    await svc.review_thesis(thesis_id=1, user_id="user-test-001", current_price=None)

    mock_qs.get_quote.assert_awaited_once_with("HPG")
    call_kwargs = mock_agent.review.call_args.kwargs
    assert call_kwargs["current_price"] == 29500.0


@pytest.mark.asyncio
async def test_review_thesis_explicit_price_skips_quote_service(mock_repo, mock_agent):
    """Explicit current_price → QuoteService never called."""
    mock_agent.review.return_value = _make_output()
    mock_qs = AsyncMock()

    svc = _make_service(mock_repo, mock_agent, quote_service=mock_qs)
    await svc.review_thesis(thesis_id=1, user_id="user-test-001", current_price=27000.0)

    mock_qs.get_quote.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_thesis_quote_service_failure_is_silent(mock_repo, mock_agent):
    """QuoteService failure must not abort the review — just log and continue."""
    mock_agent.review.return_value = _make_output()
    mock_qs = AsyncMock()
    mock_qs.get_quote.side_effect = RuntimeError("market adapter down")

    svc = _make_service(mock_repo, mock_agent, quote_service=mock_qs)
    # Should NOT raise
    result = await svc.review_thesis(thesis_id=1, user_id="user-test-001")
    assert result.verdict == ReviewVerdict.BULLISH


# ---------------------------------------------------------------------------
# review_thesis — error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_thesis_not_found_raises(mock_repo, mock_agent):
    mock_repo.get_by_id.return_value = None
    svc = _make_service(mock_repo, mock_agent)

    with pytest.raises(ThesisNotFoundError):
        await svc.review_thesis(thesis_id=999, user_id="user-test-001")

    mock_agent.review.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_thesis_wrong_user_raises(mock_repo, mock_agent):
    """Thesis owned by another user → ThesisNotFoundError (no info leak)."""
    thesis = make_thesis(user_id="other-user")
    mock_repo.get_by_id.return_value = thesis
    svc = _make_service(mock_repo, mock_agent)

    with pytest.raises(ThesisNotFoundError):
        await svc.review_thesis(thesis_id=1, user_id="user-test-001")


@pytest.mark.asyncio
async def test_review_thesis_invalidated_raises(mock_repo, mock_agent):
    """INVALIDATED thesis → ReviewNotAllowedError."""
    thesis = make_thesis(status=ThesisStatus.INVALIDATED)
    mock_repo.get_by_id.return_value = thesis
    svc = _make_service(mock_repo, mock_agent)

    with pytest.raises(ReviewNotAllowedError):
        await svc.review_thesis(thesis_id=1, user_id="user-test-001")


@pytest.mark.asyncio
async def test_review_thesis_closed_raises(mock_repo, mock_agent):
    """CLOSED thesis → ReviewNotAllowedError."""
    thesis = make_thesis(status=ThesisStatus.CLOSED)
    mock_repo.get_by_id.return_value = thesis
    svc = _make_service(mock_repo, mock_agent)

    with pytest.raises(ReviewNotAllowedError):
        await svc.review_thesis(thesis_id=1, user_id="user-test-001")


# ---------------------------------------------------------------------------
# list_reviews
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_reviews_returns_list(mock_repo, mock_agent):
    reviews = [make_review(), make_review()]
    reviews[1].id = 2
    mock_repo.list_reviews_by_thesis.return_value = reviews

    svc = _make_service(mock_repo, mock_agent)
    result = await svc.list_reviews(thesis_id=1, user_id="user-test-001")

    assert len(result) == 2
    mock_repo.list_reviews_by_thesis.assert_awaited_once_with(1, limit=10)


@pytest.mark.asyncio
async def test_list_reviews_wrong_user_raises(mock_repo, mock_agent):
    thesis = make_thesis(user_id="another")
    mock_repo.get_by_id.return_value = thesis
    svc = _make_service(mock_repo, mock_agent)

    with pytest.raises(ThesisNotFoundError):
        await svc.list_reviews(thesis_id=1, user_id="user-test-001")


@pytest.mark.asyncio
async def test_list_reviews_custom_limit(mock_repo, mock_agent):
    mock_repo.list_reviews_by_thesis.return_value = []
    svc = _make_service(mock_repo, mock_agent)
    await svc.list_reviews(thesis_id=1, user_id="user-test-001", limit=5)
    mock_repo.list_reviews_by_thesis.assert_awaited_once_with(1, limit=5)
