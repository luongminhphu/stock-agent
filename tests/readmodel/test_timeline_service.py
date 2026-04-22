"""Tests for readmodel.TimelineService."""

from __future__ import annotations

import pytest

from src.readmodel.timeline_service import TimelineService
from src.thesis.service import CreateThesisInput, ThesisNotFoundError, ThesisService

USER = "tl_user"


async def test_timeline_thesis_not_found_raises(session):
    svc = TimelineService(session)
    with pytest.raises(ThesisNotFoundError):
        await svc.get_timeline(thesis_id=99999, user_id=USER)


async def test_timeline_created_event_present(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(user_id=USER, ticker="VCB", title="Bank thesis")
    )
    await session.flush()

    svc = TimelineService(session)
    resp = await svc.get_timeline(thesis_id=thesis.id, user_id=USER)
    kinds = [e.kind for e in resp.events]
    assert "created" in kinds


async def test_timeline_review_event_added(session):
    from src.ai.agents.thesis_review import ThesisReviewAgent
    from src.thesis.review_service import ReviewService
    from tests.ai.conftest import MockPerplexityClient

    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="FPT", title="Tech"))
    await session.flush()

    mock = MockPerplexityClient(
        {
            "verdict": "NEUTRAL",
            "confidence": 0.5,
            "risk_signals": [],
            "next_watch_items": [],
            "reasoning": "Mixed signals.",
            "assumption_updates": [],
            "catalyst_status": [],
        }
    )
    review_svc = ReviewService(session=session, agent=ThesisReviewAgent(mock))
    await review_svc.review_thesis(thesis_id=thesis.id, user_id=USER)
    await session.flush()

    svc = TimelineService(session)
    resp = await svc.get_timeline(thesis_id=thesis.id, user_id=USER)
    kinds = [e.kind for e in resp.events]
    assert "reviewed" in kinds


async def test_timeline_invalidation_event(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="NVL", title="RE play"))
    await session.flush()
    await thesis_svc.invalidate(thesis_id=thesis.id, user_id=USER)
    await session.flush()

    svc = TimelineService(session)
    resp = await svc.get_timeline(thesis_id=thesis.id, user_id=USER)
    kinds = [e.kind for e in resp.events]
    assert "invalidated" in kinds


async def test_timeline_ordered_oldest_first(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(user_id=USER, ticker="MSN", title="Consumer")
    )
    await session.flush()
    await thesis_svc.close(thesis_id=thesis.id, user_id=USER)
    await session.flush()

    svc = TimelineService(session)
    resp = await svc.get_timeline(thesis_id=thesis.id, user_id=USER)
    timestamps = [e.ts for e in resp.events]
    assert timestamps == sorted(timestamps)


async def test_timeline_no_cross_user_access(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(user_id="owner", ticker="HPG", title="Steel")
    )
    await session.flush()

    svc = TimelineService(session)
    with pytest.raises(Exception):
        await svc.get_timeline(thesis_id=thesis.id, user_id=USER)


async def test_timeline_ticker_and_title_returned(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        CreateThesisInput(user_id=USER, ticker="TCB", title="Bank short")
    )
    await session.flush()

    svc = TimelineService(session)
    resp = await svc.get_timeline(thesis_id=thesis.id, user_id=USER)
    assert resp.ticker == "TCB"
    assert resp.title == "Bank short"
