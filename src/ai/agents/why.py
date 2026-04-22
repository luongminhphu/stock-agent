"""WhyAgent — explains price movement for a single ticker.
Owner: ai segment.
Caller: market segment's WhyService.
"""

from __future__ import annotations
import json
from pydantic import ValidationError
from src.ai.client import PerplexityClient, PerplexityError
from src.ai.prompts.why import SYSTEM_PROMPT, build_why_prompt
from src.ai.schemas import WhyOutput
from src.platform.logging import get_logger

logger = get_logger(__name__)


class WhyAgent:
    def __init__(self, client: PerplexityClient) -> None:
        self._client = client

    async def explain(
        self,
        ticker: str,
        company_name: str,
        sector: str,
        change_pct: float,
        price: float,
        volume: int | None,
        ohlcv_summary: str,
        extra_context: str = "",
    ) -> WhyOutput:
        prompt = build_why_prompt(
            ticker=ticker,
            company_name=company_name,
            sector=sector,
            change_pct=change_pct,
            price=price,
            volume=volume,
            ohlcv_summary=ohlcv_summary,
            extra_context=extra_context,
        )
        logger.info("why_agent.start", ticker=ticker, change_pct=change_pct)
        try:
            response = await self._client.chat_completion(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "WhyOutput",
                        "schema": WhyOutput.model_json_schema(),
                        "strict": True,
                    },
                },
            )
            data = json.loads(self._client.extract_text(response))
            result = WhyOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("why_agent.parse_error", ticker=ticker, error=str(exc))
            raise ValueError(f"Failed to parse WhyAgent response: {exc}") from exc
        except PerplexityError:
            logger.error("why_agent.api_error", ticker=ticker)
            raise

        logger.info("why_agent.complete", ticker=ticker, direction=result.direction)
        return result
