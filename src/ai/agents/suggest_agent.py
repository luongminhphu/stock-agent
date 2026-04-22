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
  "ticker": "<MÃ_CỔ_PHIẾU>",
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

_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "thesis_suggestion",
        "schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "thesis_title": {"type": "string"},
                "thesis_summary": {"type": "string"},
                "entry_price_hint": {"type": ["number", "null"]},
                "target_price_hint": {"type": ["number", "null"]},
                "stop_loss_hint": {"type": ["number", "null"]},
                "assumptions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["description", "rationale"],
                        "additionalProperties": False,
                    },
                },
                "catalysts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "expected_timeline": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["description", "expected_timeline", "rationale"],
                        "additionalProperties": False,
                    },
                },
                "confidence": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": [
                "ticker",
                "thesis_title",
                "thesis_summary",
                "entry_price_hint",
                "target_price_hint",
                "stop_loss_hint",
                "assumptions",
                "catalysts",
                "confidence",
                "reasoning",
            ],
            "additionalProperties": False,
        },
    },
}


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
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _pre_clean(text: str) -> str:
    """Clean AI artifacts TRƯỚC json.loads() để tránh parse error.

    Handles:
    - Citation markers: ,  — sonar-pro inject vào giữa string
    - Trailing commas trước } hoặc ] — sonar thỉnh thoảng sinh ra
    - Literal newlines bên trong JSON string values — gây JSONDecodeError
    """
    # Strip citations
    text = re.sub(r"\[\d+\]", "", text)
    # Trailing commas: ,\n} hoặc ,\n]
    text = re.sub(r",\s*(\n\s*[}\]])", r"\1", text)
    # Escape literal newlines inside string values
    text = _escape_newlines_in_strings(text)
    return text


def _escape_newlines_in_strings(text: str) -> str:
    """Escape literal newlines that appear inside JSON string values.

    json.loads() rejects raw newline characters inside strings.
    This replaces them with \\n so the JSON is valid.
    """
    result = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue
        if ch == "\\":
            result.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ch == "\n":
            result.append("\\n")
            continue
        if in_string and ch == "\r":
            result.append("\\r")
            continue
        result.append(ch)
    return "".join(result)


def _strip_citations(text: str) -> str:
    """Remove citation markers like [1], [2] that sonar-pro injects."""
    return re.sub(r"\[\d+\]", "", text).strip()


def _normalize_list(
    items: list,
    description_key: str = "description",
    extra_keys: list[str] | None = None,
) -> list[dict]:
    """Coerce a list of strings OR dicts into a list of dicts."""
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
            normalized = {
                k: _strip_citations(v) if isinstance(v, str) else v
                for k, v in item.items()
            }
            result.append(normalized)
    return result


def _normalize_data(data: dict, ticker: str) -> dict:
    """Normalise raw AI JSON dict before Pydantic validation."""
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

        json_str = ""
        try:
            # Gọi trực tiếp — KHÔNG dùng `async with self._client`
            # vì client là singleton được quản lý bởi bootstrap/shutdown.
            # async with sẽ gọi aclose() và phá singleton sau request đầu tiên.
            response = await self._client.chat_completion(
                messages=messages,
                temperature=0.2,
                max_tokens=3072,
                response_format=_RESPONSE_SCHEMA,
            )
            raw_text = self._client.extract_text(response)

            json_str = _extract_json(raw_text)
            json_str = _pre_clean(json_str)
            data = json.loads(json_str)
            data = _normalize_data(data, ticker)
            result = ThesisSuggestionResult.model_validate(data)

        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error(
                "suggest_agent.parse_error",
                ticker=ticker,
                error=str(exc),
                raw_json=json_str[:500],
            )
            raise ValueError(f"AI response for {ticker} could not be parsed: {exc}") from exc
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
