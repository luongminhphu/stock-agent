"""Unit tests for InvestorAgent."""

from __future__ import annotations

import pytest

from src.ai.agents.investor import InvestorAgent
from src.ai.schemas import RiskLevel, Verdict
from tests.ai.conftest import MockPerplexityClient


async def test_analyze_returns_typed_output(investor_payload):
    agent = InvestorAgent(MockPerplexityClient(investor_payload))
    result = await agent.analyze(ticker="VNM")
    assert result.ticker == "VNM"
    assert result.verdict == Verdict.NEUTRAL
    assert result.confidence == pytest.approx(0.6)
    assert result.risk_level == RiskLevel.MEDIUM


async def test_analyze_key_positives_negatives(investor_payload):
    agent = InvestorAgent(MockPerplexityClient(investor_payload))
    result = await agent.analyze(ticker="VNM")
    assert len(result.key_positives) == 2
    assert len(result.key_negatives) == 2


async def test_analyze_with_context(investor_payload):
    """Extra context string must be accepted and forwarded to client."""
    mock = MockPerplexityClient(investor_payload)
    agent = InvestorAgent(mock)
    await agent.analyze(ticker="VNM", context="Consider recent dairy sector headwinds")
    user_msg = mock.calls[0]["messages"][1]["content"]
    assert "dairy sector" in user_msg


async def test_analyze_ticker_in_user_message(investor_payload):
    mock = MockPerplexityClient(investor_payload)
    agent = InvestorAgent(mock)
    await agent.analyze(ticker="VNM")
    user_msg = mock.calls[0]["messages"][1]["content"]
    assert "VNM" in user_msg


async def test_analyze_raises_on_bad_json():
    class _BrokenClient:
        async def chat_completion(self, **_):
            return {"choices": [{"message": {"content": "{{broken"}}]}

        def extract_text(self, r):
            return r["choices"][0]["message"]["content"]

    agent = InvestorAgent(_BrokenClient())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Failed to parse"):
        await agent.analyze(ticker="VNM")


async def test_analyze_raises_on_invalid_verdict():
    bad_payload = {
        "ticker": "VNM",
        "verdict": "STRONG_BUY",  # not a valid Verdict
        "confidence": 0.9,
        "risk_level": "LOW",
        "summary": "...",
    }
    agent = InvestorAgent(MockPerplexityClient(bad_payload))
    with pytest.raises(ValueError, match="Failed to parse"):
        await agent.analyze(ticker="VNM")


async def test_analyze_summary_non_empty(investor_payload):
    agent = InvestorAgent(MockPerplexityClient(investor_payload))
    result = await agent.analyze(ticker="VNM")
    assert result.summary != ""


async def test_analyze_uses_json_response_format(investor_payload):
    """Client must be called with response_format json_object."""
    mock = MockPerplexityClient(investor_payload)
    agent = InvestorAgent(mock)
    await agent.analyze(ticker="VNM")
    assert mock.calls[0].get("response_format") == {"type": "json_object"}
