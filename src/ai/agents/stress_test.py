"""Stress-Test Agent — adversarial thesis validator.

Owner: ai segment.
Caller: thesis.stress_test_service — passes all thesis context,
receives typed StressTestOutput back.

Prompt strategy: adversarial framing.
The agent is instructed to act as a bearish analyst whose job is to
find every reason the thesis could be wrong — not to confirm it.
This produces stronger counter-arguments than a neutral review.

Prompt pack lives in src/ai/prompts/stress_test.py — edit prompts there,
not here. This module owns only the API call + parse logic.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from src.ai.client import PerplexityClient, PerplexityError
from src.ai.prompts.stress_test import SYSTEM_PROMPT as _SYSTEM_PROMPT
from src.ai.prompts.stress_test import build_user_prompt as _build_user_prompt
from src.ai.schemas import StressTestOutput
from src.platform.logging import get_logger

logger = get_logger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _extract_json(text: str) -> str:
    text = text.strip()
    match = _JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text


class StressTestAgent:
    """Adversarial AI agent for stress-testing investment thesis assumptions.

    Owner: ai segment.
    Caller: thesis.StressTestService.

    This agent does NOT know thesis business rules or DB schema.
    It receives pre-built context strings and returns StressTestOutput.
    """

    def __init__(self, client: PerplexityClient) -> None:
        self._client = client

    async def stress_test(
        self,
        ticker: str,
        thesis_title: str,
        thesis_summary: str,
        assumptions: list[dict],
        catalysts: list[str] | None = None,
        current_price: float | None = None,
        entry_price: float | None = None,
        target_price: float | None = None,
        stop_loss: float | None = None,
        macro_context: str = "",
    ) -> StressTestOutput:
        """Run adversarial stress-test and return structured output.

        Args:
            assumptions: list of dicts with keys: id, description, status.
            catalysts:   list of pending catalyst descriptions.
            macro_context: pre-built string with current price + sector context.

        Raises:
            PerplexityError: API call failed after retries.
            ValueError: Response cannot be parsed into StressTestOutput.
        """
        prompt = _build_user_prompt(
            ticker=ticker,
            thesis_title=thesis_title,
            thesis_summary=thesis_summary,
            assumptions=assumptions,
            catalysts=catalysts or [],
            current_price=current_price,
            entry_price=entry_price,
            target_price=target_price,
            stop_loss=stop_loss,
            macro_context=macro_context,
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        logger.info("stress_test_agent.start", ticker=ticker, thesis_title=thesis_title)

        raw_text = ""
        try:
            response = await self._client.chat_completion(
                messages=messages,
                temperature=0.2,  # Low temp: consistent adversarial output, not random
            )
            raw_text = self._client.extract_text(response)
            clean_text = _extract_json(raw_text)
            logger.debug(
                "stress_test_agent.raw_response",
                ticker=ticker,
                raw_length=len(raw_text),
            )
            data = json.loads(clean_text)
            result = StressTestOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error(
                "stress_test_agent.parse_error",
                ticker=ticker,
                error=str(exc),
                raw_text=raw_text[:500],
            )
            raise ValueError(
                f"Failed to parse stress-test response for {ticker}: {exc}"
            ) from exc
        except PerplexityError:
            logger.error("stress_test_agent.api_error", ticker=ticker)
            raise

        logger.info(
            "stress_test_agent.complete",
            ticker=ticker,
            verdict=result.verdict,
            invalidation_prob=result.invalidation_probability,
            threatened_count=len(result.threatened_assumptions),
        )
        return result
