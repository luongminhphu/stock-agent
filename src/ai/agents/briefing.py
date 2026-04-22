"""BriefingAgent — generates morning and end-of-day market briefs.

Owner: ai segment.
Caller: briefing segment's BriefingService.

This agent does NOT know watchlist business rules or user preferences;
those are resolved by BriefingService before calling this agent.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from src.ai.client import PerplexityClient, PerplexityError
from src.ai.prompts.brief import (
    SYSTEM_PROMPT,
    build_eod_prompt,
    build_morning_prompt,
)
from src.ai.schemas import BriefOutput
from src.platform.logging import get_logger

logger = get_logger(__name__)


class BriefingAgent:
    """AI agent for generating market briefs.

    Supports two brief types:
    - morning_brief: pre-market context, watchlist scan priming
    - eod_brief: end-of-day recap, P&L context, next-day prep
    """

    def __init__(self, client: PerplexityClient) -> None:
        self._client = client

    async def morning_brief(
        self,
        market_context: str,
        watchlist_tickers: list[str],
        extra_context: str = "",
    ) -> BriefOutput:
        """Generate a pre-market morning brief.

        Args:
            market_context: Overnight/pre-market price summary string.
            watchlist_tickers: Tickers from user's watchlist to focus on.
            extra_context: Optional free-text context (news, macro events).

        Returns:
            Typed BriefOutput.

        Raises:
            ValueError: If response cannot be parsed.
            PerplexityError: On API failure.
        """
        return await self._run_brief(
            brief_type="morning",
            prompt=build_morning_prompt(
                market_context=market_context,
                watchlist_tickers=watchlist_tickers,
                extra_context=extra_context,
            ),
        )

    async def eod_brief(
        self,
        market_context: str,
        watchlist_tickers: list[str],
        extra_context: str = "",
    ) -> BriefOutput:
        """Generate an end-of-day brief.

        Args:
            market_context: Intraday summary, net foreign flow, index performance.
            watchlist_tickers: Tickers to review for the day.
            extra_context: Optional free-text context.

        Returns:
            Typed BriefOutput.

        Raises:
            ValueError: If response cannot be parsed.
            PerplexityError: On API failure.
        """
        return await self._run_brief(
            brief_type="eod",
            prompt=build_eod_prompt(
                market_context=market_context,
                watchlist_tickers=watchlist_tickers,
                extra_context=extra_context,
            ),
        )

    async def _run_brief(self, brief_type: str, prompt: str) -> BriefOutput:
        """Shared execution path for both brief types."""
        logger.info("briefing_agent.start", brief_type=brief_type)

        try:
            response = await self._client.chat_completion(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "BriefOutput",
                        "schema": BriefOutput.model_json_schema(),
                        "strict": True,
                    },
                },
            )
            raw_text = self._client.extract_text(response)
            data = json.loads(raw_text)
            result = BriefOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("briefing_agent.parse_error", brief_type=brief_type, error=str(exc))
            raise ValueError(f"Failed to parse AI response for {brief_type} brief: {exc}") from exc
        except PerplexityError:
            logger.error("briefing_agent.api_error", brief_type=brief_type)
            raise

        logger.info(
            "briefing_agent.complete",
            brief_type=brief_type,
            sentiment=result.sentiment,
        )
        return result
