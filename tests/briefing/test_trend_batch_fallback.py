"""TrendBatchScheduler dùng TrendReasoningAgent.fallback() (public) khi reasoning
raise — không còn gọi private _rule_based_fallback xuyên segment (Wave F8c).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.ai.agents.trend_reasoning import TrendReasoningAgent
from src.ai.schemas.trend_prediction import (
    SignalLabel,
    SignalScore,
    TechnicalSignalBundle,
    TrendRegime,
    TrendVerdict,
)
from src.briefing.trend_batch_scheduler import TrendBatchScheduler
from src.market.trend_engine import RULE_FALLBACK_TAG


class _ExplodingAgent(TrendReasoningAgent):
    """analyze() raise ngoài tầm try/except nội bộ — mô phỏng lỗi không phải AIError."""

    def __init__(self) -> None:
        super().__init__(client=object())

    async def analyze(self, bundle: Any, thesis_context: str = "N/A") -> Any:
        raise ValueError("boom")


def _bundle(symbol: str, composite: float) -> TechnicalSignalBundle:
    s = SignalScore(value=0.5, label=SignalLabel.NEUTRAL)
    return TechnicalSignalBundle(
        symbol=symbol,
        momentum=s,
        structure=s,
        volume=s,
        volatility=s,
        composite=composite,
        regime=TrendRegime.RANGING,
    )


@pytest.mark.asyncio
async def test_reasoning_batch_uses_public_fallback_per_failed_bundle() -> None:
    stub: Any = object()
    scheduler = TrendBatchScheduler(
        trend_engine=stub,
        reasoning_agent=_ExplodingAgent(),
        prediction_store=stub,
        watchlist_service=None,
    )
    bundles = [_bundle("HPG", 0.80), _bundle("VNM", 0.10)]

    preds = await scheduler._run_reasoning_batch(bundles, session=None, user_id="u1")

    assert [p.symbol for p in preds] == ["HPG", "VNM"]
    assert preds[0].verdict is TrendVerdict.STRONG_BUY
    assert preds[1].verdict is TrendVerdict.STRONG_SELL
    assert all(p.reasoning.startswith(RULE_FALLBACK_TAG) for p in preds)
    # Guardrail: fallback không đủ confidence để đẩy STRONG alert (0.68)
    assert all(p.confidence < 0.68 for p in preds)
