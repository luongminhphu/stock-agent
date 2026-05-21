"""TrendReasoningAgent — AI-backed trend verdict from TechnicalSignalBundle.

Owner: ai segment.
Input:  TechnicalSignalBundle (from market segment, via DTO — no direct import of OHLCVService)
Output: TrendPrediction (src.ai.prompts.trend_reasoning)

Lifecycle:
    Singleton registered in bootstrap. Access via get_trend_reasoning_agent().

Wave 2 wiring:
    bootstrap.py registers _trend_reasoning_agent
    bot/commands/trend.py calls get_trend_reasoning_agent().analyze(bundle)
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.ai.client import AIClient, AIError
from src.ai.prompts.trend_reasoning import (
    TrendPrediction,
    build_trend_prompt,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

_STALE_HOURS = 4


class TrendReasoningAgent:
    """Wrap AIClient.chat() to produce TrendPrediction from a TechnicalSignalBundle.

    Args:
        client: AIClient singleton from bootstrap.
    """

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def analyze(
        self,
        bundle,
        thesis_summary: str = "N/A",
    ) -> TrendPrediction:
        """Run AI reasoning on a TechnicalSignalBundle.

        Args:
            bundle: TechnicalSignalBundle from market.trend_engine
            thesis_summary: optional thesis context string (plain text)

        Returns:
            TrendPrediction — AI-generated, confidence capped at 0.85.

        Raises:
            AIError: propagated to caller; bot layer should catch and fallback.
        """
        system_prompt, user_prompt = build_trend_prompt(bundle, thesis_summary)

        logger.info(
            "trend_reasoning_agent.start",
            symbol=bundle.symbol,
            regime=bundle.regime,
            composite=bundle.composite,
        )

        prediction: TrendPrediction = await self._client.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=TrendPrediction,
            temperature=0.2,
        )

        # Enforce hard cap on confidence regardless of what model returns
        if prediction.confidence > 0.85:
            prediction = prediction.model_copy(update={"confidence": 0.85})

        # Stamp symbol in case model forgets to echo it
        if not prediction.symbol:
            prediction = prediction.model_copy(update={"symbol": bundle.symbol})

        # Check staleness against bundle timestamp
        age_hours = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - bundle.as_of.replace(tzinfo=None)
        ).total_seconds() / 3600
        if age_hours > _STALE_HOURS:
            prediction = prediction.model_copy(update={"is_stale": True})

        logger.info(
            "trend_reasoning_agent.done",
            symbol=bundle.symbol,
            verdict=prediction.verdict,
            confidence=prediction.confidence,
            is_stale=prediction.is_stale,
        )
        return prediction
