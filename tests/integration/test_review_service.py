"""Integration tests for thesis.ReviewService.

Uses:
 - in-memory SQLite (from conftest.py)
 - MockPerplexityClient (from tests/ai/conftest.py)
 - Real ThesisReviewAgent, ReviewService, ThesisService, ThesisRepository

No HTTP calls. Tests the full flow:
  create thesis → review_thesis() → persist ThesisReview → assert fields
"""
from __future__ import annotations

import pytest

from src.ai.agents.thesis_review import ThesisReviewAgent
from src.ai.schemas import Verdict
from src.thesis.models import ReviewVerdict, ThesisStatus
from src.thesis.review_service import ReviewNotAllowedError, ReviewService
from src.thesis.service import CreateThesisInput, ThesisNotFoundError, ThesisService
from tests.ai.conftest import MockPerplexityClient

USER = "integration_user"


def _make_agent(verdict: str = "BULLISH", confidence: float = 0.8) -> ThesisReviewAgent:
    payload = {
        "verdict": verdict,
        "confidence": confidence,
        "risk_signals": ["Margin risk"],
        "next_watch_items": ["Q3 earnings"],
        "reasoning": "Thesis intact based on current data.",
        "assumption_updates": [],
        "catalyst_status": [],
    }
    return ThesisReviewAgent(MockPerplexityClient(payload))


async def test_review_persists_record(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(
            user_id=USER,
            ticker="HPG",
            title="Steel recovery",
            entry_price=20_000,
            target_price=30_000,
        )
    )
    await session.flush()

    review_svc = ReviewService(session=session, agent=_make_agent())
    review = await review_svc.review_thesis(
        thesis_id=thesis.id,
        user_id=USER,
        current_price=22_000,
    )
    await session.flush()

    assert review.id is not None
    assert review.thesis_id == thesis.id
    assert review.verdict == ReviewVerdict.BULLISH
    assert review.confidence == pytest.approx(0.8)
    assert review.reviewed_price == pytest.approx(22_000)
    assert "intact" in review.reasoning


async def test_review_stores_risk_signals_as_json(session):
    import json

    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(user_id=USER, ticker="VNM", title="Dairy rerating")
    )
    await session.flush()

    review_svc = ReviewService(session=session, agent=_make_agent())
    review = await review_svc.review_thesis(thesis_id=thesis.id, user_id=USER)
    await session.flush()

    signals = json.loads(review.risk_signals or "[]")
    assert isinstance(signals, list)
    assert len(signals) >= 1


async def test_review_wrong_user_raises(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(user_id=USER, ticker="FPT", title="Tech play")
    )
    await session.flush()

    review_svc = ReviewService(session=session, agent=_make_agent())
    with pytest.raises(ThesisNotFoundError):
        await review_svc.review_thesis(thesis_id=thesis.id, user_id="other_user")


async def test_review_closed_thesis_raises(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(user_id=USER, ticker="MWG", title="Retail recovery")
    )
    await session.flush()
    await thesis_svc.close(thesis_id=thesis.id, user_id=USER)
    await session.flush()

    review_svc = ReviewService(session=session, agent=_make_agent())
    with pytest.raises(ReviewNotAllowedError):
        await review_svc.review_thesis(thesis_id=thesis.id, user_id=USER)


async def test_review_invalidated_thesis_raises(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(user_id=USER, ticker="NVL", title="RE bottom")
    )
    await session.flush()
    await thesis_svc.invalidate(thesis_id=thesis.id, user_id=USER)
    await session.flush()

    review_svc = ReviewService(session=session, agent=_make_agent())
    with pytest.raises(ReviewNotAllowedError):
        await review_svc.review_thesis(thesis_id=thesis.id, user_id=USER)


async def test_multiple_reviews_accumulate(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(user_id=USER, ticker="VCB", title="Bank thesis")
    )
    await session.flush()

    review_svc = ReviewService(session=session, agent=_make_agent())
    await review_svc.review_thesis(thesis_id=thesis.id, user_id=USER)
    await session.flush()
    await review_svc.review_thesis(thesis_id=thesis.id, user_id=USER)
    await session.flush()

    reviews = await review_svc.list_reviews(thesis_id=thesis.id, user_id=USER)
    assert len(reviews) == 2


async def test_review_bearish_verdict_stored(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(user_id=USER, ticker="TCB", title="Bank short thesis")
    )
    await session.flush()

    review_svc = ReviewService(
        session=session,
        agent=_make_agent(verdict="BEARISH", confidence=0.65),
    )
    review = await review_svc.review_thesis(thesis_id=thesis.id, user_id=USER)
    await session.flush()

    assert review.verdict == ReviewVerdict.BEARISH
    assert review.confidence == pytest.approx(0.65)


async def test_review_with_assumptions_and_catalysts(session):
    """Thesis with assumptions+catalysts should pass context to agent."""
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(
            user_id=USER,
            ticker="HPG",
            title="Full context review",
            assumptions=["Steel demand holds", "USD/VND stable"],
            catalysts=["Q2 earnings", "Infrastructure spend"],
        )
    )
    await session.flush()

    mock_client = MockPerplexityClient({
        "verdict": "BULLISH",
        "confidence": 0.7,
        "risk_signals": [],
        "next_watch_items": [],
        "reasoning": "Context received.",
        "assumption_updates": ["Steel demand holds — confirmed"],
        "catalyst_status": ["Q2 earnings pending"],
    })
    agent = ThesisReviewAgent(mock_client)
    review_svc = ReviewService(session=session, agent=agent)
    await review_svc.review_thesis(thesis_id=thesis.id, user_id=USER)
    await session.flush()

    # Verify agent received non-empty messages
    user_msg = mock_client.calls[0]["messages"][1]["content"]
    assert "Steel demand holds" in user_msg
    assert "Q2 earnings" in user_msg
