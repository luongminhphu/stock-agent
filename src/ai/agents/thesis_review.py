import json
import re

from pydantic import ValidationError

from src.ai.client import PerplexityClient, PerplexityError
from src.ai.prompts.thesis_review import SYSTEM_PROMPT, build_review_prompt
from src.ai.schemas import ThesisReviewOutput
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Matches ```json ... ``` or ``` ... ``` fences that sonar-pro wraps output in
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _extract_json(text: str) -> str:
    """Strip markdown code fences if present, else return text as-is."""
    text = text.strip()
    match = _JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
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
        assumptions: list[str],
        catalysts: list[str],
        triggered_catalysts: list[str] | None = None,
        current_price: float | None = None,
        entry_price: float | None = None,
        target_price: float | None = None,
        **_: object,
    ) -> ThesisReviewOutput:
        """Run a thesis review and return structured output.

        Args:
            catalysts:            PENDING catalysts — chưa xảy ra, sắp tới.
            triggered_catalysts:  TRIGGERED catalysts — đã kích hoạt/xảy ra.
                                  Mặc định None (tương đương list rỗng).
            assumptions_with_ids: Optional richer assumption payload from thesis segment.
                                  Currently accepted for compatibility, not used by prompt yet.

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
                    assumptions=assumptions,
                    catalysts=catalysts,
                    triggered_catalysts=triggered_catalysts or [],
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
