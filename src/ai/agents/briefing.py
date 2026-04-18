"""Briefing agent — thin AI wrapper for morning/EOD market briefs.

Owner: ai segment.
Business rules such as which tickers to include, how to build market context,
and when to trigger a brief belong to the briefing segment.
"""
from __future__ import annotations

import json

from src.ai.client import PerplexityClient
from src.ai.prompts.brief import (
    EOD_SYSTEM_PROMPT,
    MORNING_SYSTEM_PROMPT,
    build_eod_prompt,
    build_morning_prompt,
)
from src.ai.schemas import BriefOutput


class BriefingAgent:
    """Generate structured morning and end-of-day market briefs."""

    def __init__(
        self,
        client: PerplexityClient,
        model: str = "sonar",
    ) -> None:
        self._client = client
        self._model = model

    async def generate_morning_brief(
        self,
        market_context: str,
        watchlist_tickers: list[str],
    ) -> BriefOutput:
        return await self._generate(
            system_prompt=MORNING_SYSTEM_PROMPT,
            user_prompt=build_morning_prompt(market_context, watchlist_tickers),
        )

    async def generate_eod_brief(
        self,
        market_context: str,
        watchlist_tickers: list[str],
    ) -> BriefOutput:
        return await self._generate(
            system_prompt=EOD_SYSTEM_PROMPT,
            user_prompt=build_eod_prompt(market_context, watchlist_tickers),
        )

    async def _generate(self, system_prompt: str, user_prompt: str) -> BriefOutput:
        response = await self._client.chat_completion(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = self._client.extract_text(response)
        try:
            return BriefOutput.model_validate_json(content)
        except Exception:
            data = json.loads(content)
            return BriefOutput.model_validate(data)
