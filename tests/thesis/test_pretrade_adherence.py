"""Wave E3b — DecisionService.reconcile_pretrade_with_action."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.platform.db import AsyncSessionLocal
from src.thesis.decision_service import (
    ADHERENCE_FOLLOWED,
    ADHERENCE_IGNORED,
    DecisionService,
)
from src.thesis.models import DecisionLog


async def _advice(
    session, user_id: str, ticker: str, verdict: str, days_ago: int = 1
) -> DecisionLog:
    row = DecisionLog(
        thesis_id=None,
        user_id=user_id,
        ticker=ticker,
        decision_type="PRETRADE_ADVICE",
        decision_at=datetime.now(UTC) - timedelta(days=days_ago),
        active_signal=verdict,
        rationale="test",
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.anyio
async def test_buy_against_bearish_is_ignored_and_idempotent() -> None:
    async with AsyncSessionLocal() as s:
        advice = await _advice(s, "u-adh", "HPG", "BEARISH")
        svc = DecisionService(s)
        got = await svc.reconcile_pretrade_with_action(
            user_id="u-adh", ticker="hpg", action_type="BUY"
        )
        assert got is not None and got.id == advice.id
        assert got.adherence == ADHERENCE_IGNORED and got.adherence_action_at is not None
        # lần 2: advice đã reconcile → không còn candidate
        assert (
            await svc.reconcile_pretrade_with_action(
                user_id="u-adh", ticker="HPG", action_type="SELL"
            )
            is None
        )


@pytest.mark.anyio
async def test_sell_with_bearish_is_followed_and_window_respected() -> None:
    async with AsyncSessionLocal() as s:
        await _advice(s, "u-adh2", "VCB", "BEARISH", days_ago=30)  # ngoài cửa sổ 7 ngày
        recent = await _advice(s, "u-adh2", "VCB", "BEARISH", days_ago=2)
        svc = DecisionService(s)
        got = await svc.reconcile_pretrade_with_action(
            user_id="u-adh2", ticker="VCB", action_type="SELL"
        )
        assert got is not None and got.id == recent.id and got.adherence == ADHERENCE_FOLLOWED


@pytest.mark.anyio
async def test_non_trade_action_returns_none() -> None:
    async with AsyncSessionLocal() as s:
        await _advice(s, "u-adh3", "FPT", "BULLISH")
        assert (
            await DecisionService(s).reconcile_pretrade_with_action(
                user_id="u-adh3", ticker="FPT", action_type="DEFER"
            )
            is None
        )
