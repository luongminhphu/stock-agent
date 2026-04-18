"""Shared fixtures for thesis segment unit tests.

Strategy:
- All services receive fake/stub dependencies (no real DB, no real AI)
- AsyncMock for ThesisReviewAgent
- MagicMock for ThesisRepository
- Thesis ORM objects built via _make_thesis() helper (no DB)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.thesis.models import (
    Assumption,
    AssumptionStatus,
    Catalyst,
    CatalystStatus,
    ReviewVerdict,
    Thesis,
    ThesisReview,
    ThesisStatus,
)


# ---------------------------------------------------------------------------
# Builder helpers — construct ORM-like objects without a DB session
# ---------------------------------------------------------------------------


def make_assumption(
    description: str = "Steel demand grows 10% YoY",
    status: AssumptionStatus = AssumptionStatus.VALID,
    thesis_id: int = 1,
) -> Assumption:
    a = Assumption(
        description=description,
        status=status,
    )
    a.id = 1
    a.thesis_id = thesis_id
    a.updated_at = datetime.now(timezone.utc)
    return a


def make_catalyst(
    description: str = "Q2 earnings beat",
    status: CatalystStatus = CatalystStatus.PENDING,
    thesis_id: int = 1,
) -> Catalyst:
    c = Catalyst(
        description=description,
        status=status,
    )
    c.id = 1
    c.thesis_id = thesis_id
    return c


def make_review(
    verdict: ReviewVerdict = ReviewVerdict.BULLISH,
    confidence: float = 0.80,
    thesis_id: int = 1,
) -> ThesisReview:
    r = ThesisReview(
        thesis_id=thesis_id,
        verdict=verdict,
        confidence=confidence,
        reasoning="Strong fundamentals",
        risk_signals='["iron ore price"]',
        next_watch_items='["Q2 earnings"]',
        reviewed_at=datetime.now(timezone.utc),
        reviewed_price=27000.0,
    )
    r.id = 1
    return r


def make_thesis(
    ticker: str = "HPG",
    status: ThesisStatus = ThesisStatus.ACTIVE,
    entry_price: float | None = 25000.0,
    target_price: float | None = 35000.0,
    stop_loss: float | None = 22000.0,
    assumptions: list[Assumption] | None = None,
    catalysts: list[Catalyst] | None = None,
    reviews: list[ThesisReview] | None = None,
    user_id: str = "user-test-001",
    thesis_id: int = 1,
) -> Thesis:
    t = Thesis(
        user_id=user_id,
        ticker=ticker,
        title=f"{ticker} long thesis",
        summary=f"Bullish on {ticker} due to strong macro.",
        status=status,
        entry_price=entry_price,
        target_price=target_price,
        stop_loss=stop_loss,
    )
    t.id = thesis_id
    t.score = None
    t.assumptions = assumptions or []
    t.catalysts = catalysts or []
    t.reviews = reviews or []
    t.snapshots = []
    t.created_at = datetime.now(timezone.utc)
    t.updated_at = datetime.now(timezone.utc)
    t.closed_at = None
    return t


# ---------------------------------------------------------------------------
# Mock agent fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_agent():
    """AsyncMock for ThesisReviewAgent."""
    agent = AsyncMock()
    return agent


@pytest.fixture()
def mock_session():
    """MagicMock AsyncSession — never touches a real DB."""
    return MagicMock()


@pytest.fixture()
def mock_repo():
    """AsyncMock ThesisRepository pre-wired with a default active thesis."""
    repo = AsyncMock()
    thesis = make_thesis()
    repo.get_by_id.return_value = thesis
    repo.list_reviews_by_thesis.return_value = []
    repo.save_review.return_value = None
    return repo
