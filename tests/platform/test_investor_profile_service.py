"""Unit tests for InvestorProfileService.build_snapshot().

Uses SQLite in-memory via the shared `session` fixture in conftest.py.
No real DB, no network calls, no AI calls — pure data aggregation logic.

Test matrix:
    1. empty DB → returns snapshot with zeros/empty lists
    2. with active theses → active_thesis_count correct
    3. with CORRECT decisions in last 30d → win_rate_30d calculated
    4. with INCORRECT decisions → win_rate_30d = 0
    5. with key_lessons set → top_lessons populated
    6. with pattern_detected set → behavioral_patterns populated
    7. with open positions + sector → portfolio_bias string
    8. get_latest() → returns most recent snapshot
    9. get_investor_context() → returns InvestorContext with static + snapshot
   10. to_prompt_block() → contains profile fields
"""

from __future__ import annotations

import datetime
import json

import pytest

from src.platform.investor_profile import (
    InvestorContext,
    InvestorProfileService,
    StaticProfile,
    _parse_json_list,
)

USER_ID = "test-user-001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_thesis(session, ticker: str, status_value: str = "active"):
    """Insert a minimal Thesis row. Imports model lazily to avoid circular deps."""
    from src.thesis.models import Thesis, ThesisStatus

    t = Thesis(
        user_id=USER_ID,
        ticker=ticker,
        title=f"{ticker} thesis",
        summary="test",
        status=ThesisStatus(status_value),
        entry_price=100_000,
        stop_loss=90_000,
        target_price=120_000,
    )
    session.add(t)
    await session.flush()
    return t


async def _make_decision(
    session,
    verdict_value: str | None,
    key_lesson: str = "",
    pattern: str = "",
    days_ago: int = 5,
):
    """Insert a DecisionLog row."""
    from src.thesis.models import DecisionLog, OutcomeVerdict

    now = datetime.datetime.now(datetime.timezone.utc)
    decision_at = now - datetime.timedelta(days=days_ago)
    evaluated_at = now - datetime.timedelta(days=max(0, days_ago - 1))

    d = DecisionLog(
        user_id=USER_ID,
        ticker="VCB",
        decision_type="BUY",
        decision_at=decision_at,
        rationale="test rationale",
        outcome_verdict=OutcomeVerdict(verdict_value) if verdict_value else None,
        outcome_evaluated_at=evaluated_at if verdict_value else None,
        key_lesson=key_lesson,
        pattern_detected=pattern,
    )
    session.add(d)
    await session.flush()
    return d


async def _make_position(session, sector: str, market_value: float):
    """Insert an open Position row."""
    from src.portfolio.models import Position

    p = Position(
        user_id=USER_ID,
        ticker="VCB",
        sector=sector,
        quantity=100,
        avg_cost=market_value / 100,
        market_value=market_value,
        is_open=True,
    )
    session.add(p)
    await session.flush()
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildSnapshotEmpty:
    async def test_empty_db_returns_snapshot_with_defaults(self, session):
        svc = InvestorProfileService(session)
        snapshot = await svc.build_snapshot(user_id=USER_ID)

        assert snapshot.active_thesis_count == 0
        assert snapshot.win_rate_30d == 0.0
        assert snapshot.avg_hold_days == 0.0
        assert snapshot.portfolio_bias == ""
        assert _parse_json_list(snapshot.top_lessons) == []
        assert _parse_json_list(snapshot.behavioral_patterns) == []

    async def test_snapshot_is_added_to_session(self, session):
        svc = InvestorProfileService(session)
        snapshot = await svc.build_snapshot(user_id=USER_ID)
        await session.flush()
        assert snapshot.id is not None


class TestBuildSnapshotTheses:
    async def test_counts_only_active_theses(self, session):
        await _make_thesis(session, "VCB", "active")
        await _make_thesis(session, "MWG", "active")
        await _make_thesis(session, "HPG", "invalidated")  # should not count

        svc = InvestorProfileService(session)
        snapshot = await svc.build_snapshot(user_id=USER_ID)

        assert snapshot.active_thesis_count == 2


class TestBuildSnapshotDecisions:
    async def test_win_rate_all_correct(self, session):
        await _make_decision(session, "correct", days_ago=2)
        await _make_decision(session, "correct", days_ago=5)
        await _make_decision(session, "correct", days_ago=10)

        svc = InvestorProfileService(session)
        snapshot = await svc.build_snapshot(user_id=USER_ID)

        assert snapshot.win_rate_30d == pytest.approx(100.0)

    async def test_win_rate_mixed(self, session):
        await _make_decision(session, "correct", days_ago=3)
        await _make_decision(session, "incorrect", days_ago=6)
        await _make_decision(session, "incorrect", days_ago=10)
        await _make_decision(session, "correct", days_ago=15)

        svc = InvestorProfileService(session)
        snapshot = await svc.build_snapshot(user_id=USER_ID)

        assert snapshot.win_rate_30d == pytest.approx(50.0)

    async def test_old_decisions_excluded_from_win_rate(self, session):
        # > 30 days old — should NOT count toward win rate
        await _make_decision(session, "correct", days_ago=35)
        await _make_decision(session, "correct", days_ago=40)

        svc = InvestorProfileService(session)
        snapshot = await svc.build_snapshot(user_id=USER_ID)

        assert snapshot.win_rate_30d == 0.0  # nothing in last 30d

    async def test_top_lessons_populated(self, session):
        await _make_decision(session, "correct", key_lesson="Lesson A", days_ago=1)
        await _make_decision(session, "incorrect", key_lesson="Lesson B", days_ago=2)
        await _make_decision(session, "correct", key_lesson="", days_ago=3)  # no lesson

        svc = InvestorProfileService(session)
        snapshot = await svc.build_snapshot(user_id=USER_ID)
        lessons = _parse_json_list(snapshot.top_lessons)

        assert "Lesson A" in lessons
        assert "Lesson B" in lessons
        assert len(lessons) == 2

    async def test_behavioral_patterns_populated(self, session):
        await _make_decision(session, "incorrect", pattern="FOMO buy at resistance", days_ago=3)
        await _make_decision(session, "incorrect", pattern="Sell too early", days_ago=5)

        svc = InvestorProfileService(session)
        snapshot = await svc.build_snapshot(user_id=USER_ID)
        patterns = _parse_json_list(snapshot.behavioral_patterns)

        assert "FOMO buy at resistance" in patterns
        assert "Sell too early" in patterns


class TestBuildSnapshotPortfolio:
    async def test_portfolio_bias_calculated(self, session):
        await _make_position(session, sector="Banking", market_value=4_000_000)
        await _make_position(session, sector="Real Estate", market_value=1_000_000)

        svc = InvestorProfileService(session)
        snapshot = await svc.build_snapshot(user_id=USER_ID)

        assert "Banking" in snapshot.portfolio_bias
        assert "80%" in snapshot.portfolio_bias  # 4M / 5M = 80%

    async def test_empty_portfolio_bias_is_empty_string(self, session):
        svc = InvestorProfileService(session)
        snapshot = await svc.build_snapshot(user_id=USER_ID)
        assert snapshot.portfolio_bias == ""


class TestGetLatest:
    async def test_get_latest_returns_none_when_empty(self, session):
        svc = InvestorProfileService(session)
        assert await svc.get_latest() is None

    async def test_get_latest_returns_most_recent(self, session):
        svc = InvestorProfileService(session)
        s1 = await svc.build_snapshot(user_id=USER_ID)
        await session.flush()
        s2 = await svc.build_snapshot(user_id=USER_ID)
        await session.flush()

        latest = await svc.get_latest()
        assert latest is not None
        assert latest.id == s2.id


class TestGetInvestorContext:
    async def test_returns_investor_context_type(self, session):
        svc = InvestorProfileService(session)
        ctx = await svc.get_investor_context()
        assert isinstance(ctx, InvestorContext)

    async def test_is_empty_when_no_snapshot(self, session):
        svc = InvestorProfileService(session)
        ctx = await svc.get_investor_context()
        assert ctx.is_empty() is True

    async def test_not_empty_after_build_snapshot(self, session):
        await _make_thesis(session, "VCB", "active")
        svc = InvestorProfileService(session)
        await svc.build_snapshot(user_id=USER_ID)
        await session.flush()

        ctx = await svc.get_investor_context()
        assert ctx.snapshot is not None
        assert ctx.snapshot.active_thesis_count == 1


class TestToPromptBlock:
    async def test_prompt_block_contains_static_fields(self, session):
        """to_prompt_block() must include static profile fields from settings."""
        static = StaticProfile(
            risk_appetite="medium",
            thesis_style="fundamental",
            trading_horizon="swing",
            preferred_sectors="banking",
            avoid="penny stocks",
        )
        ctx = InvestorContext(static=static, snapshot=None)
        block = ctx.to_prompt_block()

        assert "medium" in block
        assert "fundamental" in block
        assert "banking" in block
        assert "penny stocks" in block

    async def test_prompt_block_includes_snapshot_metrics(self, session):
        """When snapshot exists, metrics appear in to_prompt_block()."""
        await _make_decision(session, "correct", key_lesson="Không FOMO", days_ago=3)
        await _make_thesis(session, "VCB", "active")

        svc = InvestorProfileService(session)
        snapshot = await svc.build_snapshot(user_id=USER_ID)
        await session.flush()

        static = StaticProfile.from_settings()
        ctx = InvestorContext(static=static, snapshot=snapshot)
        block = ctx.to_prompt_block()

        assert "Win rate" in block or "Active theses" in block


class TestParseJsonList:
    def test_valid_json_list(self):
        assert _parse_json_list('["a", "b"]') == ["a", "b"]

    def test_empty_string(self):
        assert _parse_json_list("") == []

    def test_invalid_json(self):
        assert _parse_json_list("not-json") == []

    def test_non_list_json(self):
        assert _parse_json_list('{"key": "value"}') == []
