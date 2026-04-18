"""Unit tests for readmodel Pydantic schemas."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.readmodel.schemas import (
    DashboardResponse,
    LeaderboardEntry,
    LeaderboardResponse,
    ThesisSummaryRow,
    ThesisTimelineResponse,
    TimelineEvent,
    WatchlistSnapshotRow,
)


_NOW = datetime.now(timezone.utc)


def _summary_row(**kwargs) -> ThesisSummaryRow:
    defaults = dict(
        id=1,
        ticker="HPG",
        title="Test",
        status="active",
        score=70.0,
        entry_price=50_000.0,
        target_price=65_000.0,
        stop_loss=45_000.0,
        upside_pct=30.0,
        risk_reward=3.0,
        current_price=None,
        pnl_pct=None,
        last_verdict="BULLISH",
        last_reviewed_at=None,
        created_at=_NOW,
        assumption_count=0,
        invalid_assumption_count=0,
        catalyst_count=0,
        triggered_catalyst_count=0,
    )
    defaults.update(kwargs)
    return ThesisSummaryRow(**defaults)


# ---------------------------------------------------------------------------
# ThesisSummaryRow
# ---------------------------------------------------------------------------


def test_summary_row_created():
    row = _summary_row()
    assert row.ticker == "HPG"
    assert row.current_price is None


def test_summary_row_with_live_price():
    row = _summary_row(current_price=55_000.0, pnl_pct=10.0)
    assert row.current_price == 55_000.0
    assert row.pnl_pct == 10.0


# ---------------------------------------------------------------------------
# DashboardResponse
# ---------------------------------------------------------------------------


def test_dashboard_response_counts():
    rows = [_summary_row(id=i, ticker=f"T{i:02d}") for i in range(3)]
    dashboard = DashboardResponse(
        user_id="u1",
        generated_at=_NOW,
        total_theses=3,
        active_count=2,
        invalidated_count=1,
        closed_count=0,
        avg_score=70.0,
        theses=rows,
    )
    assert dashboard.total_theses == 3
    assert len(dashboard.theses) == 3


def test_dashboard_empty():
    d = DashboardResponse(
        user_id="u1",
        generated_at=_NOW,
        total_theses=0,
        active_count=0,
        invalidated_count=0,
        closed_count=0,
        avg_score=None,
        theses=[],
    )
    assert d.avg_score is None
    assert d.theses == []


# ---------------------------------------------------------------------------
# LeaderboardResponse
# ---------------------------------------------------------------------------


def test_leaderboard_entries_ranked():
    entries = [
        LeaderboardEntry(
            rank=i + 1,
            thesis_id=i,
            ticker=f"T{i}",
            title=f"Thesis {i}",
            score=float(90 - i * 10),
            pnl_pct=float(20 - i * 5),
            last_verdict="BULLISH",
            status="active",
            created_at=_NOW,
        )
        for i in range(3)
    ]
    lb = LeaderboardResponse(user_id="u1", sort_by="score", entries=entries)
    assert lb.entries[0].rank == 1
    assert lb.entries[0].score == 90.0


# ---------------------------------------------------------------------------
# ThesisTimelineResponse
# ---------------------------------------------------------------------------


def test_timeline_events_ordered():
    events = [
        TimelineEvent(
            kind="created",
            ts=datetime(2025, 1, 1, tzinfo=timezone.utc),
            summary="Thesis created",
            detail=None,
        ),
        TimelineEvent(
            kind="reviewed",
            ts=datetime(2025, 3, 1, tzinfo=timezone.utc),
            summary="AI review: BULLISH",
            detail={"verdict": "BULLISH"},
        ),
    ]
    tl = ThesisTimelineResponse(thesis_id=1, ticker="HPG", title="Test", events=events)
    assert tl.events[0].kind == "created"
    assert tl.events[1].kind == "reviewed"


# ---------------------------------------------------------------------------
# WatchlistSnapshotRow
# ---------------------------------------------------------------------------


def test_watchlist_row_nullable_fields():
    row = WatchlistSnapshotRow(
        ticker="FPT",
        note=None,
        thesis_id=None,
        thesis_title=None,
        thesis_status=None,
        current_price=None,
        added_at=_NOW,
    )
    assert row.ticker == "FPT"
    assert row.current_price is None
