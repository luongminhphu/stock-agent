import json

from pydantic import ValidationError

from src.ai.client import PerplexityClient, PerplexityError
from src.ai.schemas import StockAnalysisOutput
from src.platform.logging import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = """Bạn là chuyên gia phân tích cổ phiếu Việt Nam (HOSE, HNX, UPCoM).
Phân tích cổ phiếu được hỏi và trả về JSON với verdict, confidence, risk_level,
các điểm tích cực/tiêu cực, và tóm tắt ngắn gọn.
Chỉ trả về JSON, không có text thừa.
"""


class InvestorAgent:
    """General-purpose stock analysis agent.

    Owner: ai segment.
    Use for on-demand ticker analysis outside of a formal thesis context.
    For thesis-specific review, use ThesisReviewAgent.
    """

    def __init__(self, client: PerplexityClient) -> None:
        self._client = client

    async def analyze(
        self,
        ticker: str,
        context: str = "",
    ) -> StockAnalysisOutput:
        """Analyze a single ticker and return structured output."""
        user_msg = f"Phân tích cổ phiếu {ticker} cho thị trường chứng khoán Việt Nam."
        if context:
            user_msg += f"\nContext bổ sung: {context}"
        user_msg += """

Trả về JSON:
{
  "ticker": "...",
  "verdict": "BULLISH|BEARISH|NEUTRAL|WATCHLIST",
  "confidence": 0.0-1.0,
  "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
  "price_target_note": "...",
  "key_positives": ["..."],
  "key_negatives": ["..."],
  "summary": "..."
}
"""

        logger.info("investor_agent.start", ticker=ticker)

        try:
            response = await self._client.chat_completion(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
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
