"""Tests for readmodel.LeaderboardService."""

from __future__ import annotations

import pytest

from src.readmodel.leaderboard_service import LeaderboardService
from src.thesis.service import CreateThesisInput, ThesisService

USER = "lb_user"


async def test_leaderboard_empty(session):
    svc = LeaderboardService(session)
    resp = await svc.get_leaderboard(user_id=USER, sort_by="score")
    assert resp.entries == []
    assert resp.user_id == USER


async def test_leaderboard_by_score_ordering(session):
    thesis_svc = ThesisService(session)
    t1 = await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="A", title="Low score"))
    t2 = await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="B", title="High score"))
    await session.flush()
    # Manually set scores via update
    t1.score = 40.0
    t2.score = 80.0
    await session.flush()

    svc = LeaderboardService(session)
    resp = await svc.get_leaderboard(user_id=USER, sort_by="score")
    assert len(resp.entries) == 2
    # Highest score first
    assert resp.entries[0].score == pytest.approx(80.0)
    assert resp.entries[1].score == pytest.approx(40.0)


async def test_leaderboard_rank_assigned(session):
    thesis_svc = ThesisService(session)
    for i in range(3):
        await thesis_svc.create(
            CreateThesisInput(user_id=USER, ticker=f"T{i}", title=f"Thesis {i}")
        )
    await session.flush()

    svc = LeaderboardService(session)
    resp = await svc.get_leaderboard(user_id=USER, sort_by="score")
    ranks = [e.rank for e in resp.entries]
    assert ranks == list(range(1, len(ranks) + 1))


async def test_leaderboard_no_cross_user_leak(session):
    thesis_svc = ThesisService(session)
    await thesis_svc.create(CreateThesisInput(user_id="other", ticker="X", title="Other"))
    await session.flush()

    svc = LeaderboardService(session)
    resp = await svc.get_leaderboard(user_id=USER, sort_by="score")
    assert all(e.ticker != "X" for e in resp.entries)


async def test_leaderboard_sort_by_pnl(session):
    thesis_svc = ThesisService(session)
    await thesis_svc.create(CreateThesisInput(user_id=USER, ticker="HPG", title="Steel"))
    await session.flush()

    svc = LeaderboardService(session)
    # Should not raise even if pnl_pct is None (no current price injected)
    resp = await svc.get_leaderboard(user_id=USER, sort_by="pnl")
    assert resp.sort_by == "pnl"
