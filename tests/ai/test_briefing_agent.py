"""Unit tests for BriefingAgent."""

from __future__ import annotations

import pytest

from src.ai.agents.briefing import BriefingAgent
from src.ai.client import AIClient, AIError
from src.ai.schemas import MarketSentiment
from tests.ai.conftest import MockPerplexityClient


async def test_generate_morning_brief_returns_typed_output(brief_payload):
    agent = BriefingAgent(MockPerplexityClient(brief_payload))
    result = await agent.morning_brief(
        market_context="VN-Index +0.3%, thanh khoản 12k tỷ",
        watchlist_tickers=["HPG", "VNM", "VCB"],
    )
    assert result.sentiment == MarketSentiment.MIXED
    assert result.headline != ""
    assert len(result.key_movers) == 3


async def test_morning_brief_watchlist_alerts(brief_payload):
    agent = BriefingAgent(MockPerplexityClient(brief_payload))
    result = await agent.morning_brief(
        market_context="VN-Index flat",
        watchlist_tickers=["HPG", "VNM"],
    )
    assert len(result.watchlist_alerts) == 2
    assert any("HPG" in alert for alert in result.watchlist_alerts)


async def test_morning_brief_action_items(brief_payload):
    agent = BriefingAgent(MockPerplexityClient(brief_payload))
    result = await agent.morning_brief(
        market_context="",
        watchlist_tickers=[],
    )
    assert len(result.action_items) >= 1


async def test_morning_brief_tickers_in_user_message(brief_payload):
    mock = MockPerplexityClient(brief_payload)
    agent = BriefingAgent(mock)
    await agent.morning_brief(
        market_context="Flat",
        watchlist_tickers=["HPG", "VCB"],
    )
    user_msg = mock.calls[0]["messages"][1]["content"]
    assert "HPG" in user_msg
    assert "VCB" in user_msg


async def test_morning_brief_raises_on_bad_payload():
    """Schema-invalid payload → AIError from the client contract (chat())."""
    agent = BriefingAgent(MockPerplexityClient({"headline": 123}))  # missing/invalid fields
    with pytest.raises(AIError, match="Failed to parse"):
        await agent.morning_brief(market_context="", watchlist_tickers=[])


async def test_eod_brief_returns_typed_output(brief_payload):
    agent = BriefingAgent(MockPerplexityClient(brief_payload))
    result = await agent.eod_brief(
        market_context="VN-Index -0.5%, khối ngoại bán ròng 200 tỷ",
        watchlist_tickers=["VCB", "TCB"],
    )
    assert result.sentiment in list(MarketSentiment)
    assert result.summary != ""


async def test_eod_brief_uses_complex_max_tokens(brief_payload):
    """Briefing output is large → agent must request COMPLEX_MAX_TOKENS."""
    mock = MockPerplexityClient(brief_payload)
    agent = BriefingAgent(mock)
    await agent.eod_brief(market_context="EOD context", watchlist_tickers=[])
    assert mock.calls[0].get("max_tokens") == AIClient.COMPLEX_MAX_TOKENS
    assert "EOD context" in mock.calls[0]["messages"][1]["content"]


async def test_brief_invalid_sentiment_raises():
    bad_payload = {
        "headline": "Test",
        "sentiment": "EUPHORIC",  # not a valid MarketSentiment
        "summary": "...",
    }
    agent = BriefingAgent(MockPerplexityClient(bad_payload))
    with pytest.raises(AIError, match="Failed to parse"):
        await agent.morning_brief(market_context="", watchlist_tickers=[])
