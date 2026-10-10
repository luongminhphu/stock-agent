"""P2a — DecisionService.log_decision: quantity + thesis_id optional."""

from __future__ import annotations

import pytest

from src.platform.db import AsyncSessionLocal
from src.thesis.decision_service import DecisionService


@pytest.mark.anyio
async def test_log_decision_without_thesis_requires_ticker() -> None:
    async with AsyncSessionLocal() as s:
        svc = DecisionService(s)
        with pytest.raises(ValueError, match="ticker is required"):
            await svc.log_decision(
                thesis_id=None, user_id="u-p2a", decision_type="SELL", rationale="x"
            )


@pytest.mark.anyio
async def test_log_decision_without_thesis_persists_quantity_and_price() -> None:
    async with AsyncSessionLocal() as s:
        svc = DecisionService(s)
        row = await svc.log_decision(
            thesis_id=None,
            user_id="u-p2a",
            ticker="hpg",
            decision_type="sell",
            rationale="Chốt lời theo kế hoạch",
            execution_price=27500,
            quantity=1000,
        )
        assert row.id is not None
        assert row.thesis_id is None
        assert row.ticker == "HPG"
        assert str(row.decision_type) in ("SELL", "DecisionType.SELL")
        assert row.quantity == 1000
        assert row.price_at_decision == 27500.0
        assert row.thesis_score_at_decision is None
