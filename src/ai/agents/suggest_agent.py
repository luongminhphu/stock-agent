"""ThesisSuggestAgent — generates a draft investment thesis for a given ticker.

Owner: ai segment.
Capability: Given a ticker symbol, call Perplexity to produce a structured
            thesis draft with assumptions and catalysts.
Caller: api/routes/thesis.py (thin adapter via DI).

This agent does NOT persist anything. It returns a ThesisSuggestionResult
that the API layer passes to the frontend. The investor confirms and saves
via the normal thesis CRUD endpoints.

Business rules (invalidation thresholds, scoring, etc.) are NOT here —
those belong to the thesis segment.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from src.ai.client import PerplexityClient, PerplexityError
from src.ai.schemas import ThesisSuggestionResult
from src.platform.logging import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích đầu tư chứng khoán Việt Nam với 15 năm kinh nghiệm.
Nhiệm vụ: xây dựng luận điểm đầu tư có cấu trúc cho cổ phiếu HOSE/HNX/UPCoM.

Yêu cầu output:
- thesis_title: tiêu đề ngắn, súc tích, nắm được luận điểm chính
- thesis_summary: 2-3 câu mô tả luận điểm, bao gồm business model đang hoạt động tốt và điều gì sẽ thay đổi
- entry_price_hint, target_price_hint, stop_loss_hint: ước lượng giá bằng VNĐ, null nếu không đủ thông tin
- assumptions: 3-5 giả định then chốt mà thesis phụ thuộc vào (description + rationale)
- catalysts: 2-4 sự kiện có thể thúc đẩy giá (description + expected_timeline + rationale)
- confidence: 0.0-1.0
- reasoning: lý do tổng thể

Quy tắc bắt buộc:
- Chỉ dùng dữ liệu thực tế, không suy diễn quá mức
- Nếu không đủ thông tin về giá, đặt các trường price = null
- QUAN TRỌNG: chỉ trả về raw JSON object, không bọc trong markdown, không có ```json, không có giải thích thêm
- Dòng đầu tiên phải là dấu '{', dòng cuối phải là dấu '}'
"""


def _build_user_prompt(ticker: str) -> str:
    return (
        f"Hãy đề xuất một investment thesis cho mã cổ phiếu **{ticker}** "
        f"niêm yết tại HOSE/HNX/UPCoM Việt Nam.\n\n"
        f"Trả về JSON theo schema đã mô tả trong system prompt. "
        f"Field `ticker` phải là '{ticker.upper()}'. "
        f"Chỉ trả về JSON thuần, bắt đầu bằng '{{' và kết thúc bằng '}}'."
    )


def _extract_json(text: str) -> str:
    """Strip markdown code fences if present, return raw JSON string."""
    # Remove ```json ... ``` or ``` ... ``` wrappers
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        return match.group(1).strip()
    # Fallback: find first '{' to last '}'
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text


class ThesisSuggestAgent:
    """AI agent that drafts an investment thesis for a ticker.

    Owner: ai segment.
    Caller: api/routes/thesis.py — receives ticker string,
            returns typed ThesisSuggestionResult.

    Does NOT persist data; does NOT know thesis domain rules.
    """

    def __init__(self, client: PerplexityClient) -> None:
        self._client = client

    async def suggest(self, ticker: str) -> ThesisSuggestionResult:
        """Generate a thesis draft for the given ticker.

        Args:
            ticker: Stock symbol (will be uppercased), e.g. "VNM", "HPG".

        Returns:
            ThesisSuggestionResult — a draft for user review, NOT auto-saved.

        Raises:
            PerplexityError: If AI API call fails after retries.
            ValueError: If AI response cannot be parsed into the schema.
        """
        ticker = ticker.upper().strip()
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(ticker)},
        ]

        logger.info("suggest_agent.start", ticker=ticker)

        try:
            # PerplexityClient requires async context manager to initialise
            # the underlying httpx.AsyncClient. Each suggest() call opens and
            # closes its own connection — acceptable for low-frequency suggest
            # calls; no connection pooling needed at this scale.
            #
            # NOTE: Perplexity API does NOT support response_format={"type": "json_object"}.
            # Valid values are only: 'text', 'json_schema', 'regex'.
            # We enforce JSON output via explicit system prompt instructions instead.
            async with self._client as client:
                response = await client.chat_completion(
                    messages=messages,
                    temperature=0.2,
                    max_tokens=2048,
                    # No response_format — rely on prompt instruction for JSON
                )
                raw_text = client.extract_text(response)

            json_str = _extract_json(raw_text)
            data = json.loads(json_str)

            # Ensure ticker field is normalised even if AI returned lowercase
            data["ticker"] = ticker

            result = ThesisSuggestionResult.model_validate(data)

        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("suggest_agent.parse_error", ticker=ticker, error=str(exc))
            raise ValueError(
                f"AI response for {ticker} could not be parsed: {exc}"
            ) from exc
        except PerplexityError:
            logger.error("suggest_agent.api_error", ticker=ticker)
            raise

        logger.info(
            "suggest_agent.complete",
            ticker=ticker,
            confidence=result.confidence,
            n_assumptions=len(result.assumptions),
            n_catalysts=len(result.catalysts),
        )
        return result
