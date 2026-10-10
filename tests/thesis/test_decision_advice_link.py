"""P2b — nối quyết định/lệnh thật với đúng PRETRADE_ADVICE (advice_id)."""

from __future__ import annotations

import pytest

from src.platform.db import AsyncSessionLocal
from src.thesis.decision_service import (
    ADHERENCE_FOLLOWED,
    ADHERENCE_IGNORED,
    DecisionService,
    _adherence_followed,
)

U = "u-p2b"


@pytest.mark.parametrize(
    ("action", "advice_action", "proceed", "verdict", "expected"),
    [
        ("SELL", "SELL", True, "BEARISH", True),  # AI nói bán được, user bán
        ("SELL", "SELL", False, "BULLISH", False),  # AI nói đừng bán, user vẫn bán
        ("HOLD", "SELL", False, "BULLISH", True),  # AI nói đừng bán, user giữ
        ("BUY", "SELL", False, "BULLISH", True),  # AI nói đừng bán, user mua thêm
        ("BUY", "BUY", True, "BULLISH", True),
        ("BUY", "BUY", False, "BEARISH", False),
        ("SELL", "BUY", True, "BULLISH", False),  # hỏi mua, AI OK, user lại bán
        ("BUY", None, None, "BULLISH", True),  # advice cũ: rule verdict
        ("SELL", None, None, "BULLISH", False),
    ],
)
def test_adherence_rule(action, advice_action, proceed, verdict, expected):
    assert (
        _adherence_followed(
            action=action, advice_action=advice_action, advice_proceed=proceed, verdict=verdict
        )
        is expected
    )


async def _advice(svc: DecisionService, ticker: str = "HPG", action: str = "SELL", proceed=False):
    return await svc.log_pretrade_advice(
        user_id=U,
        ticker=ticker,
        verdict="BULLISH",
        confidence=0.7,
        rationale="x",
        intended_action=action,
        proceed=proceed,
    )


@pytest.mark.anyio
async def test_advice_row_stores_action_and_proceed() -> None:
    async with AsyncSessionLocal() as s:
        row = await _advice(DecisionService(s))
        assert row.advice_action == "SELL" and row.advice_proceed is False


@pytest.mark.anyio
async def test_log_decision_with_advice_id_links_and_reconciles_ignored() -> None:
    async with AsyncSessionLocal() as s:
        svc = DecisionService(s)
        advice = await _advice(svc)  # AI: đừng bán
        row = await svc.log_decision(
            thesis_id=None,
            user_id=U,
            ticker="HPG",
            decision_type="SELL",
            rationale="vẫn bán",
            execution_price=27_000,
            advice_id=advice.id,
        )
        assert row.advice_id == advice.id
        await s.refresh(advice)
        assert advice.adherence == ADHERENCE_IGNORED and advice.adherence_action_at is not None


@pytest.mark.anyio
async def test_hold_decision_against_dont_sell_advice_is_followed() -> None:
    async with AsyncSessionLocal() as s:
        svc = DecisionService(s)
        advice = await _advice(svc)
        await svc.log_decision(
            thesis_id=None,
            user_id=U,
            ticker="hpg",
            decision_type="HOLD",
            rationale="nghe AI, giữ",
            advice_id=advice.id,
        )
        await s.refresh(advice)
        assert advice.adherence == ADHERENCE_FOLLOWED


@pytest.mark.anyio
async def test_advice_id_must_match_user_and_ticker() -> None:
    async with AsyncSessionLocal() as s:
        svc = DecisionService(s)
        advice = await _advice(svc, ticker="HPG")
        with pytest.raises(ValueError, match="không phải"):
            await svc.log_decision(
                thesis_id=None,
                user_id=U,
                ticker="VNM",
                decision_type="SELL",
                rationale="x",
                advice_id=advice.id,
            )
        with pytest.raises(ValueError, match="không phải"):
            await svc.log_decision(
                thesis_id=None,
                user_id="someone-else",
                ticker="HPG",
                decision_type="SELL",
                rationale="x",
                advice_id=advice.id,
            )


@pytest.mark.anyio
async def test_explicit_reconcile_is_idempotent_and_skips_window_fallback() -> None:
    async with AsyncSessionLocal() as s:
        svc = DecisionService(s)
        advice = await _advice(svc, action="BUY", proceed=True)
        got = await svc.reconcile_pretrade_with_action(
            user_id=U, ticker="HPG", action_type="BUY", advice_id=advice.id
        )
        assert got is not None and got.adherence == ADHERENCE_FOLLOWED
        # Lần 2 (listener window fallback sau khi đã reconcile tường minh) → không còn candidate
        assert (
            await svc.reconcile_pretrade_with_action(user_id=U, ticker="HPG", action_type="SELL")
            is None
        )
        # Gọi lại tường minh → trả row cũ, không đổi
        again = await svc.reconcile_pretrade_with_action(
            user_id=U, ticker="HPG", action_type="SELL", advice_id=advice.id
        )
        assert again is not None and again.adherence == ADHERENCE_FOLLOWED
