import json
import re

from pydantic import ValidationError

from src.ai.client import PerplexityClient, PerplexityError
from src.ai.prompts.thesis_review import SYSTEM_PROMPT, build_review_prompt
from src.ai.schemas import ThesisReviewOutput
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Matches ```json ... ``` or ``` ... ``` fences
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

# Max tokens for thesis review — responses include reasoning + multiple
# recommendations and regularly exceed 1200 tokens. 4096 gives headroom.
_MAX_TOKENS = 4096


def _extract_json(text: str) -> str:
    """Extract JSON object from text, handling markdown fences and extra prose.

    Strategy (in order):
    1. Strip markdown code fence via regex — handles well-formed ```json...```.
    2. Brace-scan fallback — find first '{' and last '}' in the string.
       Handles: truncated fences (no closing ```), raw JSON with surrounding
       prose, and AI responses where fence regex doesn't match.
    """
    text = text.strip()

    # Strategy 1: regex fence strip
    match = _JSON_FENCE_RE.search(text)
    if match:
        candidate = match.group(1).strip()
        if candidate.startswith("{"):
            return candidate

    # Strategy 2: brace-scan — works even when closing fence is missing
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    # No JSON object found — return as-is and let json.loads raise
    return text


class ThesisReviewAgent:
    """AI agent for reviewing a single investment thesis.

    Owner: ai segment.
    Caller: thesis.review_service — passes in all domain data,
    receives typed ThesisReviewOutput back.

    This agent does NOT know thesis business rules (invalidation thresholds,
    scoring weights). Those live in the thesis segment.
    """

    def __init__(self, client: PerplexityClient) -> None:
        self._client = client

    async def review(
        self,
        ticker: str,
        thesis_title: str,
        thesis_summary: str,
        assumptions_with_ids: list[dict[str, object]],
        catalysts_with_ids: list[dict[str, object]],
        triggered_catalysts_with_ids: list[dict[str, object]] | None = None,
        current_price: float | None = None,
        entry_price: float | None = None,
        target_price: float | None = None,
    ) -> ThesisReviewOutput:
        """Run a thesis review and return structured output.

        Args:
            assumptions_with_ids:         Active assumptions — list[{"id": int, "description": str}].
                                          AI uses id to populate AssumptionRecommendation.target_id.
            catalysts_with_ids:           PENDING catalysts — list[{"id": int, "description": str}].
            triggered_catalysts_with_ids: TRIGGERED catalysts — context only, no recommendation needed.

        Raises:
            PerplexityError: If the API call fails after retries.
            ValueError: If the response cannot be parsed into ThesisReviewOutput.
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_review_prompt(
                    ticker=ticker,
                    thesis_title=thesis_title,
                    thesis_summary=thesis_summary,
                    assumptions_with_ids=assumptions_with_ids,
                    catalysts_with_ids=catalysts_with_ids,
                    triggered_catalysts_with_ids=triggered_catalysts_with_ids or [],
                    current_price=current_price,
                    entry_price=entry_price,
                    target_price=target_price,
                ),
            },
        ]

        logger.info("thesis_review_agent.start", ticker=ticker)

        try:
            response = await self._client.chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=_MAX_TOKENS,
                response_format={"type": "json_object"},
            )
            raw_text = self._client.extract_text(response)
            clean_text = _extract_json(raw_text)
            logger.debug(
                "thesis_review_agent.raw_response",
                ticker=ticker,
                raw_length=len(raw_text),
                clean_length=len(clean_text),
            )
            data = json.loads(clean_text)
            result = ThesisReviewOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error(
                "thesis_review_agent.parse_error",
                ticker=ticker,
                error=str(exc),
                raw_text=raw_text[:500] if "raw_text" in dir() else "unavailable",
            )
            raise ValueError(f"Failed to parse AI response for {ticker}: {exc}") from exc
        except PerplexityError:
            logger.error("thesis_review_agent.api_error", ticker=ticker)
            raise

        logger.info(
            "thesis_review_agent.complete",
            ticker=ticker,
            verdict=result.verdict,
            confidence=result.confidence,
        )
        return result
