"""P2b — lệnh thật trả lời lời khuyên AI: advice_id đi xuyên portfolio → thesis.DecisionService."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.platform.db import AsyncSessionLocal
from src.portfolio.trade_usecase import TradeUseCase
from src.thesis.decision_service import ADHERENCE_IGNORED, DecisionService
from src.thesis.models import DecisionLog

U = "u-p2b-trade"


@pytest.mark.anyio
async def test_sell_with_advice_id_logs_decision_without_thesis_and_reconciles() -> None:
    async with AsyncSessionLocal() as s:
        svc = DecisionService(s)
        advice = await svc.log_pretrade_advice(
            user_id=U,
            ticker="HPG",
            verdict="BULLISH",
            confidence=0.7,
            rationale="x",
            intended_action="SELL",
            proceed=False,
        )
        await s.commit()
        uc = TradeUseCase(session=s, quote_service=None)
        with patch.object(TradeUseCase, "_emit_user_action", return_value=None):
            await uc.execute_buy(user_id=U, ticker="HPG", qty=1000, price=25_000)
            result = await uc.execute_sell(
                user_id=U, ticker="HPG", qty=1000, price=27_000, advice_id=advice.id
            )
        assert result.decision_logged is True  # không có thesis nhưng có advice → vẫn ghi
        await s.refresh(advice)
        assert advice.adherence == ADHERENCE_IGNORED
        rows = (
            await s.execute(
                DecisionLog.__table__.select().where(DecisionLog.advice_id == advice.id)
            )
        ).all()
        assert len(rows) == 1 and rows[0].decision_type == "SELL"
