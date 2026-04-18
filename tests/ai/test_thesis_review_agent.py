"""Unit tests for ThesisReviewAgent.

All tests use MockPerplexityClient — no HTTP, no API key required.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.ai.agents.thesis_review import ThesisReviewAgent
from src.ai.schemas import Verdict
from tests.ai.conftest import MockPerplexityClient


async def test_review_returns_typed_output(thesis_review_payload):
    agent = ThesisReviewAgent(MockPerplexityClient(thesis_review_payload))
    result = await agent.review(
        ticker="HPG",
        thesis_title="Steel cycle recovery",
        thesis_summary="HPG benefits from infrastructure push",
        assumptions=["Domestic demand holds"],
        catalysts=["Q2 earnings"],
    )
    assert result.verdict == Verdict.BULLISH
    assert result.confidence == pytest.approx(0.75)
    assert len(result.risk_signals) == 2
    assert len(result.next_watch_items) == 2


async def test_review_includes_reasoning(thesis_review_payload):
    agent = ThesisReviewAgent(MockPerplexityClient(thesis_review_payload))
    result = await agent.review(
        ticker="HPG",
        thesis_title="Steel cycle recovery",
        thesis_summary="...",
        assumptions=[],
        catalysts=[],
    )
    assert "Steel cycle" in result.reasoning


async def test_review_with_prices(thesis_review_payload):
    """Agent accepts optional price context without error."""
    agent = ThesisReviewAgent(MockPerplexityClient(thesis_review_payload))
    result = await agent.review(
        ticker="HPG",
        thesis_title="Steel cycle recovery",
        thesis_summary="...",
        assumptions=[],
        catalysts=[],
        current_price=22_500,
        entry_price=20_000,
        target_price=30_000,
    )
    assert result.verdict == Verdict.BULLISH


async def test_review_sends_messages_to_client(thesis_review_payload):
    """Messages list passed to client must include system + user roles."""
    mock = MockPerplexityClient(thesis_review_payload)
    agent = ThesisReviewAgent(mock)
    await agent.review(
        ticker="HPG",
        thesis_title="T",
        thesis_summary="S",
        assumptions=[],
        catalysts=[],
    )
    assert len(mock.calls) == 1
    roles = [m["role"] for m in mock.calls[0]["messages"]]
    assert roles == ["system", "user"]


async def test_review_raises_value_error_on_bad_json():
    """Malformed JSON from client must raise ValueError, not crash silently."""

    class _BrokenClient:
        async def chat_completion(self, **_):
            return {"choices": [{"message": {"content": "not valid json {{{"}}]}

        def extract_text(self, r):
            return r["choices"][0]["message"]["content"]

    agent = ThesisReviewAgent(_BrokenClient())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Failed to parse"):
        await agent.review(
            ticker="HPG",
            thesis_title="T",
            thesis_summary="S",
            assumptions=[],
            catalysts=[],
        )


async def test_review_raises_value_error_on_invalid_schema():
    """Valid JSON but wrong schema must raise ValueError."""
    bad_payload = {"verdict": "NOT_A_VALID_VERDICT", "confidence": 0.5}
    agent = ThesisReviewAgent(MockPerplexityClient(bad_payload))
    with pytest.raises(ValueError, match="Failed to parse"):
        await agent.review(
            ticker="HPG",
            thesis_title="T",
            thesis_summary="S",
            assumptions=[],
            catalysts=[],
        )


async def test_review_assumption_updates_in_output(thesis_review_payload):
    agent = ThesisReviewAgent(MockPerplexityClient(thesis_review_payload))
    result = await agent.review(
        ticker="HPG",
        thesis_title="T",
        thesis_summary="S",
        assumptions=["Domestic demand holds"],
        catalysts=[],
    )
    assert len(result.assumption_updates) == 1
    assert "Domestic demand" in result.assumption_updates[0]


async def test_review_catalyst_status_in_output(thesis_review_payload):
    agent = ThesisReviewAgent(MockPerplexityClient(thesis_review_payload))
    result = await agent.review(
        ticker="HPG",
        thesis_title="T",
        thesis_summary="S",
        assumptions=[],
        catalysts=["Infrastructure spend"],
    )
    assert len(result.catalyst_status) == 1
