"""Shared fixtures for ai agent tests.

Provides a MockPerplexityClient that returns pre-configured JSON
without making any HTTP calls.
"""

from __future__ import annotations

import json
from typing import Any

import pytest


class MockPerplexityClient:
    """Drop-in replacement for PerplexityClient in tests.

    Pass `response_payload` as a dict — it will be serialised to JSON
    and returned as the assistant message content.
    """

    def __init__(self, response_payload: dict[str, Any]) -> None:
        self._payload = response_payload
        self.calls: list[dict[str, Any]] = []  # capture call args for assertions

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append({"messages": messages, **kwargs})
        return {
            "choices": [{"message": {"content": json.dumps(self._payload)}}],
            "model": "mock",
            "usage": {"total_tokens": 0},
        }

    def extract_text(self, response: dict[str, Any]) -> str:
        return str(response["choices"][0]["message"]["content"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def thesis_review_payload() -> dict[str, Any]:
    return {
        "verdict": "BULLISH",
        "confidence": 0.75,
        "risk_signals": ["Margin compression risk", "USD/VND headwind"],
        "next_watch_items": ["Q2 earnings", "Steel price index"],
        "reasoning": "Thesis intact. Steel cycle recovery on track.",
        "assumption_updates": ["Domestic demand assumption holds"],
        "catalyst_status": ["Infrastructure spend catalyst delayed 1 quarter"],
    }


@pytest.fixture
def investor_payload() -> dict[str, Any]:
    return {
        "ticker": "VNM",
        "verdict": "NEUTRAL",
        "confidence": 0.6,
        "risk_level": "MEDIUM",
        "price_target_note": "Fair value around 70k",
        "key_positives": ["Strong brand", "Dividend yield"],
        "key_negatives": ["Volume growth slowing", "Input cost pressure"],
        "summary": "VNM is fairly valued with limited near-term catalysts.",
    }


@pytest.fixture
def brief_payload() -> dict[str, Any]:
    return {
        "headline": "VN-Index tăng nhẹ trong bối cảnh thanh khoản thấp",
        "sentiment": "MIXED",
        "summary": "Thị trường giao dịch thận trọng. Nhóm ngân hàng dẫn dắt, bất động sản phân hóa.",
        "key_movers": ["VCB", "TCB", "NVL"],
        "watchlist_alerts": ["HPG vượt MA20", "VNM tiệm cận vùng hỗ trợ"],
        "action_items": ["Review HPG thesis", "Check VNM stop-loss level"],
    }
