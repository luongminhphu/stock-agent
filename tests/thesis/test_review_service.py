"""Unit tests for ReviewService.

Zero real API calls — PerplexityClient is mocked.
Zero real DB — SQLite in-memory via AsyncSession mock.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ai.schemas import ThesisReviewOutput, Verdict
from src.thesis.models import (
    AssumptionStatus,
    CatalystStatus,
    ReviewVerdict,
    ThesisStatus,
)
from src.thesis.review_service import ReviewNotAllowedError, ReviewService
from src.thesis.service import ThesisNotFoundError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_thesis(
    thesis_id: int = 1,
    user_id: str = "user_42",
    ticker: str = "HPG",
    status: ThesisStatus = ThesisStatus.ACTIVE,
) -> MagicMock:
    thesis = MagicMock()
    thesis.id = thesis_id
    thesis.user_id = user_id
    thesis.ticker = ticker
    thesis.title = "HPG recovery thesis"
    thesis.summary = "Steel demand recovery driven by public investment"
    thesis.status = status
    thesis.entry_price = 28_000.0
    thesis.target_price = 35_000.0
    thesis.stop_loss = 24_000.0
    thesis.assumptions = [
        MagicMock(description="Public investment cycle accelerates", status=AssumptionStatus.PENDING),
        MagicMock(description="Steel price stabilises above cost", status=AssumptionStatus.VALID),
        MagicMock(description="Assume valid", status=AssumptionStatus.INVALID),  # excluded
    ]
    thesis.catalysts = [
        MagicMock(description="New housing decree passes", status=CatalystStatus.PENDING),
        MagicMock(description="Q2 earnings beat", status=CatalystStatus.TRIGGERED),
        MagicMock(description="Old catalyst", status=CatalystStatus.EXPIRED),  # excluded
    ]
    return thesis


def _make_ai_output(verdict: Verdict = Verdict.BULLISH) -> ThesisReviewOutput:
    return ThesisReviewOutput(
        verdict=verdict,
        confidence=0.75,
        risk_signals=["Global steel oversupply", "USD strengthening"],
        next_watch_items=["Monthly public investment disbursement", "HPG inventory level"],
        reasoning="Public investment is the key catalyst; steel margins stabilising.",
        assumption_updates=["Monitor steel price weekly"],
        catalyst_status=["Housing decree expected Q3 2026"],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_review_thesis_success() -> None:
    """Happy path: agent returns structured output, review is persisted."""
    thesis = _make_thesis()
    ai_output = _make_ai_output(Verdict.BULLISH)

    mock_repo = AsyncMock()
    mock_repo.get_by_id.return_value = thesis
    mock_repo.save_review.return_value = AsyncMock()

    mock_agent = AsyncMock()
    mock_agent.review.return_value = ai_output

    mock_session = AsyncMock()

    with patch("src.thesis.review_service.ThesisRepository", return_value=mock_repo):
        svc = ReviewService(session=mock_session, agent=mock_agent)
        review = await svc.review_thesis(thesis_id=1, user_id="user_42")

    mock_agent.review.assert_called_once()
    call_kwargs = mock_agent.review.call_args.kwargs
    assert call_kwargs["ticker"] == "HPG"
    assert len(call_kwargs["assumptions"]) == 2   # INVALID excluded
    assert len(call_kwargs["catalysts"]) == 2     # EXPIRED excluded

    mock_repo.save_review.assert_called_once()
    saved = mock_repo.save_review.call_args.args[0]
    assert saved.verdict == ReviewVerdict.BULLISH
    assert saved.confidence == 0.75


async def test_review_thesis_not_found() -> None:
    mock_repo = AsyncMock()
    mock_repo.get_by_id.return_value = None
    mock_agent = AsyncMock()
    mock_session = AsyncMock()

    with patch("src.thesis.review_service.ThesisRepository", return_value=mock_repo):
        svc = ReviewService(session=mock_session, agent=mock_agent)
        with pytest.raises(ThesisNotFoundError):
            await svc.review_thesis(thesis_id=999, user_id="user_42")

    mock_agent.review.assert_not_called()


async def test_review_thesis_wrong_owner() -> None:
    thesis = _make_thesis(user_id="other_user")
    mock_repo = AsyncMock()
    mock_repo.get_by_id.return_value = thesis
    mock_agent = AsyncMock()
    mock_session = AsyncMock()

    with patch("src.thesis.review_service.ThesisRepository", return_value=mock_repo):
        svc = ReviewService(session=mock_session, agent=mock_agent)
        with pytest.raises(ThesisNotFoundError):
            await svc.review_thesis(thesis_id=1, user_id="user_42")


async def test_review_thesis_not_active() -> None:
    thesis = _make_thesis(status=ThesisStatus.CLOSED)
    mock_repo = AsyncMock()
    mock_repo.get_by_id.return_value = thesis
    mock_agent = AsyncMock()
    mock_session = AsyncMock()

    with patch("src.thesis.review_service.ThesisRepository", return_value=mock_repo):
        svc = ReviewService(session=mock_session, agent=mock_agent)
        with pytest.raises(ReviewNotAllowedError):
            await svc.review_thesis(thesis_id=1, user_id="user_42")

    mock_agent.review.assert_not_called()


async def test_review_thesis_filters_invalid_assumptions_and_expired_catalysts() -> None:
    """Verify INVALID assumptions and EXPIRED catalysts are excluded from agent call."""
    thesis = _make_thesis()
    mock_repo = AsyncMock()
    mock_repo.get_by_id.return_value = thesis
    mock_repo.save_review.return_value = AsyncMock()
    mock_agent = AsyncMock()
    mock_agent.review.return_value = _make_ai_output()
    mock_session = AsyncMock()

    with patch("src.thesis.review_service.ThesisRepository", return_value=mock_repo):
        svc = ReviewService(session=mock_session, agent=mock_agent)
        await svc.review_thesis(thesis_id=1, user_id="user_42")

    kwargs = mock_agent.review.call_args.kwargs
    assumption_texts = kwargs["assumptions"]
    catalyst_texts = kwargs["catalysts"]

    assert not any("Assume valid" in t for t in assumption_texts), "INVALID assumption must be excluded"
    assert not any("Old catalyst" in t for t in catalyst_texts), "EXPIRED catalyst must be excluded"


async def test_price_enrichment_from_quote_service() -> None:
    """When current_price not provided, ReviewService fetches from QuoteService."""
    thesis = _make_thesis()
    mock_repo = AsyncMock()
    mock_repo.get_by_id.return_value = thesis
    mock_repo.save_review.return_value = AsyncMock()

    mock_quote = MagicMock(price=29_500.0)
    mock_quote_svc = AsyncMock()
    mock_quote_svc.get_quote.return_value = mock_quote

    mock_agent = AsyncMock()
    mock_agent.review.return_value = _make_ai_output()
    mock_session = AsyncMock()

    with patch("src.thesis.review_service.ThesisRepository", return_value=mock_repo):
        svc = ReviewService(session=mock_session, agent=mock_agent, quote_service=mock_quote_svc)
        await svc.review_thesis(thesis_id=1, user_id="user_42")

    mock_quote_svc.get_quote.assert_called_once_with("HPG")
    saved = mock_repo.save_review.call_args.args[0]
    assert saved.reviewed_price == 29_500.0


async def test_price_enrichment_fallback_on_quote_error() -> None:
    """If QuoteService fails, review continues without price (reviewed_price=None)."""
    thesis = _make_thesis()
    mock_repo = AsyncMock()
    mock_repo.get_by_id.return_value = thesis
    mock_repo.save_review.return_value = AsyncMock()

    mock_quote_svc = AsyncMock()
    mock_quote_svc.get_quote.side_effect = RuntimeError("market down")

    mock_agent = AsyncMock()
    mock_agent.review.return_value = _make_ai_output()
    mock_session = AsyncMock()

    with patch("src.thesis.review_service.ThesisRepository", return_value=mock_repo):
        svc = ReviewService(session=mock_session, agent=mock_agent, quote_service=mock_quote_svc)
        await svc.review_thesis(thesis_id=1, user_id="user_42")  # must not raise

    saved = mock_repo.save_review.call_args.args[0]
    assert saved.reviewed_price is None
