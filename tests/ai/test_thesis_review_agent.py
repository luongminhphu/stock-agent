"""Unit tests for ThesisReviewAgent.

All tests use MockPerplexityClient — no HTTP, no API key required.
Contract under test: ``review(..., assumptions_with_ids, catalysts_with_ids, ...)``
→ ``ThesisReviewOutput`` (overall_verdict / conviction_score / key_risks /
action_recommendation / *_recommendations).
"""

from __future__ import annotations

import pytest

from src.ai.agents.thesis_review import ThesisReviewAgent
from src.ai.schemas import Verdict
from tests.ai.conftest import MockPerplexityClient

_ASSUMPTIONS = [{"id": 1, "description": "Domestic demand holds"}]
_CATALYSTS = [{"id": 1, "description": "Q2 earnings"}]


def _review(agent: ThesisReviewAgent, **overrides):
    kwargs = dict(
        ticker="HPG",
        thesis_title="Steel cycle recovery",
        thesis_summary="HPG benefits from infrastructure push",
        assumptions_with_ids=_ASSUMPTIONS,
        catalysts_with_ids=_CATALYSTS,
    )
    kwargs.update(overrides)
    return agent.review(**kwargs)


async def test_review_returns_typed_output(thesis_review_payload):
    agent = ThesisReviewAgent(MockPerplexityClient(thesis_review_payload))
    result = await _review(agent)
    assert result.overall_verdict == Verdict.BULLISH
    assert result.confidence == pytest.approx(0.75)
    assert result.conviction_score == pytest.approx(0.8)
    assert len(result.key_risks) == 2
    assert result.action_recommendation == "HOLD"


async def test_review_includes_summary(thesis_review_payload):
    agent = ThesisReviewAgent(MockPerplexityClient(thesis_review_payload))
    result = await _review(agent, assumptions_with_ids=[], catalysts_with_ids=[])
    assert "Steel cycle" in result.summary


async def test_review_with_prices(thesis_review_payload):
    """Agent accepts optional price context without error."""
    agent = ThesisReviewAgent(MockPerplexityClient(thesis_review_payload))
    result = await _review(
        agent,
        current_price=22_500,
        entry_price=20_000,
        target_price=30_000,
    )
    assert result.overall_verdict == Verdict.BULLISH


async def test_review_sends_messages_to_client(thesis_review_payload):
    """Messages list passed to client must include system + user roles and context."""
    mock = MockPerplexityClient(thesis_review_payload)
    agent = ThesisReviewAgent(mock)
    await _review(agent)
    assert len(mock.calls) == 1
    roles = [m["role"] for m in mock.calls[0]["messages"]]
    assert roles == ["system", "user"]
    user_msg = mock.calls[0]["messages"][1]["content"]
    assert "HPG" in user_msg
    assert "Domestic demand holds" in user_msg
    assert "Q2 earnings" in user_msg


async def test_review_raises_value_error_on_bad_json():
    """Malformed JSON from client must raise ValueError, not crash silently."""

    class _BrokenClient:
        async def chat_completion(self, **_):
            return {"choices": [{"message": {"content": "not valid json {{{"}}]}

        def extract_text(self, r):
            return r["choices"][0]["message"]["content"]

    agent = ThesisReviewAgent(_BrokenClient())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Failed to parse"):
        await _review(agent)


async def test_review_raises_value_error_on_invalid_schema():
    """Valid JSON but wrong schema must raise ValueError."""
    bad_payload = {"overall_verdict": "NOT_A_VALID_VERDICT", "confidence": 0.5}
    agent = ThesisReviewAgent(MockPerplexityClient(bad_payload))
    with pytest.raises(ValueError, match="Failed to parse"):
        await _review(agent)


async def test_review_legacy_alias_keys_coerced():
    """Older model output (verdict/reasoning/risk_signals) is still accepted."""
    legacy = {
        "verdict": "bearish",
        "confidence": 0.6,
        "reasoning": "Demand weakening.",
        "risk_signals": ["Export quota"],
    }
    agent = ThesisReviewAgent(MockPerplexityClient(legacy))
    result = await _review(agent)
    assert result.overall_verdict == Verdict.BEARISH
    assert result.conviction_score == pytest.approx(0.6)
    assert result.summary == "Demand weakening."
    assert result.key_risks == ["Export quota"]
    assert result.action_recommendation == "HOLD"  # default when absent


async def test_review_assumption_recommendations_in_output(thesis_review_payload):
    agent = ThesisReviewAgent(MockPerplexityClient(thesis_review_payload))
    result = await _review(agent)
    assert len(result.assumption_recommendations) == 1
    rec = result.assumption_recommendations[0]
    assert rec.assumption_id == 1
    assert rec.status == "VALID"
    assert "Domestic demand" in rec.evidence


async def test_review_catalyst_recommendations_in_output(thesis_review_payload):
    agent = ThesisReviewAgent(MockPerplexityClient(thesis_review_payload))
    result = await _review(agent)
    assert len(result.catalyst_recommendations) == 1
    rec = result.catalyst_recommendations[0]
    assert rec.catalyst_id == 1
    assert rec.status == "DELAYED"
