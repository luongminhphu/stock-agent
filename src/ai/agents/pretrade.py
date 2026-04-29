"""PreTradeAgent — cross-checks thesis, scan signal, brief before a trade.
Owner: ai segment.
Caller: thesis segment's PreTradeService.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from src.ai.client import PerplexityClient, PerplexityError
from src.ai.prompts.pretrade import SYSTEM_PROMPT, build_pretrade_prompt
from src.ai.schemas import PreTradeCheckOutput
from src.platform.logging import get_logger

logger = get_logger(__name__)


class PreTradeAgent:
    def __init__(self, client: PerplexityClient) -> None:
        self._client = client

    async def check(
        self,
        ticker: str,
        price: float,
        change_pct: float,
        thesis_context: str,
        signal_context: str,
        brief_context: str,
    ) -> PreTradeCheckOutput:
        prompt = build_pretrade_prompt(
            ticker=ticker,
            price=price,
            change_pct=change_pct,
            thesis_context=thesis_context,
            signal_context=signal_context,
            brief_context=brief_context,
        )
        logger.info("pretrade_agent.start", ticker=ticker)
        try:
            response = await self._client.chat_completion(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "PreTradeCheckOutput",
                        "schema": PreTradeCheckOutput.model_json_schema(),
                        "strict": True,
                    },
                },
            )
            data = json.loads(self._client.extract_text(response))
            result = PreTradeCheckOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("pretrade_agent.parse_error", ticker=ticker, error=str(exc))
            raise ValueError(f"Failed to parse PreTradeAgent response: {exc}") from exc
        except PerplexityError:
            logger.error("pretrade_agent.api_error", ticker=ticker)
            raise

        logger.info(
            "pretrade_agent.complete",
            ticker=ticker,
            decision=result.decision,
            confidence=result.confidence,
        )
        return result
