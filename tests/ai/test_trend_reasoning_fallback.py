"""TrendReasoningAgent.fallback() delegate về market.rule_based_prediction (Wave F8b).

Đảm bảo /trend (bot), batch (briefing) và agent cho cùng một verdict khi AI lỗi.
"""

from __future__ import annotations

import pytest

from src.ai.agents.trend_reasoning import TrendReasoningAgent
from src.ai.schemas.trend_prediction import (
    SignalLabel,
    SignalScore,
    TechnicalSignalBundle,
    TrendRegime,
    TrendVerdict,
)
from src.market.trend_engine import RULE_FALLBACK_TAG, rule_based_prediction


class _FailingClient:
    async def chat(self, **_: object) -> object:
        raise RuntimeError("llm down")


def _bundle(composite: float) -> TechnicalSignalBundle:
    s = SignalScore(value=0.5, label=SignalLabel.NEUTRAL)
    return TechnicalSignalBundle(
        symbol="FPT",
        momentum=s,
        structure=s,
        volume=s,
        volatility=s,
        composite=composite,
        regime=TrendRegime.TRENDING_UP,
    )


@pytest.mark.asyncio
async def test_analyze_falls_back_to_market_rule_when_llm_fails() -> None:
    agent = TrendReasoningAgent(client=_FailingClient())
    bundle = _bundle(0.75)

    pred = await agent.analyze(bundle)
    expected = rule_based_prediction(bundle)

    assert pred.verdict is TrendVerdict.STRONG_BUY
    assert pred.verdict is expected.verdict
    assert pred.direction is expected.direction
    assert pred.confidence == expected.confidence
    assert pred.reasoning.startswith(RULE_FALLBACK_TAG)


def test_fallback_is_public_and_matches_market_rule() -> None:
    agent = TrendReasoningAgent(client=_FailingClient())
    bundle = _bundle(0.30)
    assert agent.fallback(bundle).model_dump(exclude={"generated_at"}) == rule_based_prediction(
        bundle
    ).model_dump(exclude={"generated_at"})
