"""Tests for readmodel.ThesisTimelineService — current contract.

Contract (as of Wave 12.1 rewrite):
- `get_timeline(thesis_id)` returns ThesisTimelineResponse, or None when the
  thesis id does not exist (read-only projection — it does NOT raise
  ThesisNotFoundError and does NOT enforce cross-user access; ownership
  checks are the responsibility of the API/route layer).
"""

from __future__ import annotations

from src.readmodel.timeline_service import ThesisTimelineService
from src.thesis.service import CreateThesisInput, ThesisService

USER = "tl_user"


async def test_timeline_not_found_returns_none(session):
    svc = ThesisTimelineService(session)
    resp = await svc.get_timeline(thesis_id=99999)
    assert resp is None


async def test_timeline_created_event_present(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        USER, CreateThesisInput(ticker="VCB", title="Bank thesis")
    )
    await session.flush()

    svc = ThesisTimelineService(session)
    resp = await svc.get_timeline(thesis_id=thesis.id)
    assert resp is not None
    kinds = [e.kind for e in resp.events]
    assert "created" in kinds


async def test_timeline_ordered_oldest_first(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        USER, CreateThesisInput(ticker="MSN", title="Consumer")
    )
    await session.flush()

    svc = ThesisTimelineService(session)
    resp = await svc.get_timeline(thesis_id=thesis.id)
    assert resp is not None
    timestamps = [e.ts for e in resp.events]
    assert timestamps == sorted(timestamps)


async def test_timeline_ticker_returned(session):
    thesis_svc = ThesisService(session)
    thesis = await thesis_svc.create(
        USER, CreateThesisInput(ticker="TCB", title="Bank short")
    )
    await session.flush()

    svc = ThesisTimelineService(session)
    resp = await svc.get_timeline(thesis_id=thesis.id)
    assert resp is not None
    assert resp.ticker == "TCB"
