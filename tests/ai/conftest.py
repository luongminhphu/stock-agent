"""Shared fixtures for ai agent tests.

Provides a MockPerplexityClient that returns pre-configured JSON
without making any HTTP calls.
"""

from __future__ import annotations

import json
from typing import Any

import pytest


class MockPerplexityClient:
    """Drop-in replacement for ``src.ai.client.AIClient`` in tests.

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

    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[Any],
        **kwargs: Any,
    ) -> Any:
        """Mirror ``AIClient.chat`` — parse the canned payload into ``response_schema``."""
        self.calls.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                **kwargs,
            }
        )
        try:
            return response_schema.model_validate(self._payload)
        except Exception as exc:  # same contract as AIClient.chat
            from src.ai.client import AIError

            raise AIError(
                f"Failed to parse response into {response_schema.__name__}: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def thesis_review_payload() -> dict[str, Any]:
    """Payload theo contract ThesisReviewOutput hiện tại (src/ai/schemas/thesis_review.py)."""
    return {
        "overall_verdict": "BULLISH",
        "conviction_score": 0.8,
        "confidence": 0.75,
        "key_risks": ["Margin compression risk", "USD/VND headwind"],
        "action_recommendation": "HOLD",
        "summary": "Thesis intact. Steel cycle recovery on track.",
        "assumption_recommendations": [
            {
                "assumption_id": 1,
                "status": "VALID",
                "evidence": "Domestic demand assumption holds",
                "confidence": 0.7,
            }
        ],
        "catalyst_recommendations": [
            {
                "catalyst_id": 1,
                "status": "DELAYED",
                "updated_timeline": "Q4/2026",
                "notes": "Infrastructure spend catalyst delayed 1 quarter",
                "confidence": 0.6,
            }
        ],
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
