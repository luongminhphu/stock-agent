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

Yêu cầu output JSON với cấu trúc chính xác sau:
{
  "ticker": "<MÃ_CỔ_PHIẼU>",
  "thesis_title": "<tiêu đề ngắn súc tích>",
  "thesis_summary": "<2-3 câu mô tả luận điểm>",
  "entry_price_hint": <số hoặc null>,
  "target_price_hint": <số hoặc null>,
  "stop_loss_hint": <số hoặc null>,
  "assumptions": [
    {"description": "<giả định>", "rationale": "<lý do>"},
    ...
  ],
  "catalysts": [
    {"description": "<sự kiện>", "expected_timeline": "<VD: Q3 2025>", "rationale": "<lý do>"},
    ...
  ],
  "confidence": <0.0 — 1.0>,
  "reasoning": "<lý do tổng thể>"
}

Quy tắc bắt buộc:
- TOÀN BỘ nội dung text (thesis_title, thesis_summary, assumptions, catalysts, reasoning) PHẢI viết bằng tiếng Việt
- Đây là hệ thống phân tích TTCK Việt Nam (HOSE/HNX/UPCoM), không dùng tiếng Anh trong output
- assumptions và catalysts PHẢI là array of objects có các field trên, KHÔNG được là array of string
- Chỉ dùng dữ liệu thực tế, không suy diễn quá mức
- Nếu không đủ thông tin về giá, đặt các trường price = null
- Chỉ trả về raw JSON object, không bọc trong markdown, không có ```json
- Dòng đầu tiên phải là dấu '{', dòng cuối phải là dấu '}'
"""


def _build_user_prompt(ticker: str) -> str:
    return (
        f"Hãy đề xuất một investment thesis cho mã cổ phiếu **{ticker}** "
        f"niêm yết tại HOSE/HNX/UPCoM Việt Nam.\n\n"
        f"Trả về JSON theo đúng cấu trúc đã mô tả trong system prompt. "
        f"Tất cả nội dung phải bằng tiếng Việt. "
        f"Field `ticker` phải là '{ticker.upper()}'. "
        f"assumptions và catalysts phải là array of objects, không phải array of string."
    )


def _extract_json(text: str) -> str:
    """Strip markdown code fences if present, return raw JSON string."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        return match.group(1).strip()
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text


def _strip_citations(text: str) -> str:
    """Remove citation markers like [1], [2] that sonar-pro injects."""
    return re.sub(r"\[\d+\]", "", text).strip()


def _normalize_list(
    items: list,
    description_key: str = "description",
    extra_keys: list[str] | None = None,
) -> list[dict]:
    """Coerce a list of strings OR dicts into a list of dicts.

    sonar-pro sometimes returns assumptions/catalysts as plain strings
    instead of {description, rationale} objects. This normalizer handles
    both shapes so Pydantic validation never sees a bare string.
    """
    result = []
    for item in items:
        if isinstance(item, str):
            cleaned = _strip_citations(item)
            obj: dict = {description_key: cleaned, "rationale": ""}
            if extra_keys:
                for k in extra_keys:
                    obj.setdefault(k, "")
            result.append(obj)
        elif isinstance(item, dict):
            # Strip citations from all string values
            normalized = {
                k: _strip_citations(v) if isinstance(v, str) else v
                for k, v in item.items()
            }
            result.append(normalized)
        # skip None / unexpected types
    return result


def _normalize_data(data: dict, ticker: str) -> dict:
    """Normalise raw AI JSON dict before Pydantic validation.

    Handles:
    - assumptions as list[str] → list[{description, rationale}]
    - catalysts as list[str] → list[{description, expected_timeline, rationale}]
    - citation markers stripped from all string fields
    - ticker forced to uppercase
    """
    data["ticker"] = ticker

    if isinstance(data.get("assumptions"), list):
        data["assumptions"] = _normalize_list(
            data["assumptions"],
            description_key="description",
        )

    if isinstance(data.get("catalysts"), list):
        data["catalysts"] = _normalize_list(
            data["catalysts"],
            description_key="description",
            extra_keys=["expected_timeline"],
        )

    # Strip citations from top-level string fields
    for field in ("thesis_title", "thesis_summary", "reasoning"):
        if isinstance(data.get(field), str):
            data[field] = _strip_citations(data[field])

    return data


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
            # the underlying httpx.AsyncClient.
            # NOTE: Perplexity API only supports response_format types:
            # 'text', 'json_schema', 'regex' — NOT 'json_object'.
            # JSON output is enforced via prompt instructions.
            async with self._client as client:
                response = await self._client.chat_completion(
                    messages=messages,
                    temperature=0.2,
                    max_tokens=2048,
                )
                raw_text = self._client.extract_text(response)

            json_str = _extract_json(raw_text)
            data = json.loads(json_str)
            data = _normalize_data(data, ticker)
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
