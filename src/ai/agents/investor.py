import json

from pydantic import ValidationError

from src.ai.client import PerplexityClient, PerplexityError
from src.ai.prompts.investor import SYSTEM_PROMPT, build_user_prompt
from src.ai.schemas import StockAnalysisOutput
from src.platform.logging import get_logger

logger = get_logger(__name__)


class InvestorAgent:
    """General-purpose stock analysis agent.

    Owner: ai segment.
    Use for on-demand ticker analysis outside of a formal thesis context.
    For thesis-specific review, use ThesisReviewAgent.

    Prompts: src/ai/prompts/investor.py
    """

    def __init__(self, client: PerplexityClient) -> None:
        self._client = client

    async def analyze(
        self,
        ticker: str,
        context: str = "",
    ) -> StockAnalysisOutput:
        """Analyze a single ticker and return structured output."""
        logger.info("investor_agent.start", ticker=ticker)

        try:
            response = await self._client.chat_completion(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(ticker, context)},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            raw_text = self._client.extract_text(response)
            data = json.loads(raw_text)
            result = StockAnalysisOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("investor_agent.parse_error", ticker=ticker, error=str(exc))
            raise ValueError(f"Failed to parse AI response for {ticker}: {exc}") from exc
        except PerplexityError:
            logger.error("investor_agent.api_error", ticker=ticker)
            raise

        logger.info("investor_agent.complete", ticker=ticker, verdict=result.verdict)
        return result
