"""ThesisSuggestAgent — generate a draft investment thesis for a given ticker.

Owner: ai segment.
Callers:
    - bot/commands/suggest (Discord /suggest command)
    - api routes (future)

Boundary:
    - Accepts ticker string + optional context strings.
    - Returns ThesisDraft (Pydantic schema, owned by ai segment).
    - Does NOT write DB, does NOT call thesis/watchlist repositories.
    - Caller (bot or service) is responsible for persisting the draft if desired.

Note on schema:
    ThesisDraft is intentionally a flat, AI-segment-owned schema.
    It maps to thesis.models.ThesisDraft for persistence, but the mapping
    happens in the caller — not here.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field
from pydantic import ValidationError

from src.ai.client import AIClient, AIError
from src.platform.logging import get_logger

logger = get_logger(__name__)


class AssumptionDraft(BaseModel):
    description: str
    invalidation_signal: str


class CatalystDraft(BaseModel):
    description: str
    expected_timeframe: str = Field(
        ..., description="SHORT_TERM | MEDIUM_TERM | LONG_TERM"
    )


class ThesisDraft(BaseModel):
    """Structured thesis draft produced by the AI.

    Owner: ai segment.
    Mapped to thesis.models.ThesisDraft by the calling service.
    """

    ticker: str
    title: str
    summary: str
    entry_price_suggestion: float | None = None
    target_price_suggestion: float | None = None
    stop_loss_suggestion: float | None = None
    time_horizon: str = Field(
        ..., description="SHORT_TERM | MEDIUM_TERM | LONG_TERM"
    )
    assumptions: list[AssumptionDraft] = Field(
        ..., min_length=1, description="At least 1 assumption required"
    )
    catalysts: list[CatalystDraft] = Field(
        ..., min_length=1, description="At least 1 catalyst required"
    )
    risk_summary: str
    conviction_level: str = Field(
        ..., description="HIGH | MEDIUM | LOW"
    )


_SYSTEM_PROMPT = """
Bạn là chuyên gia phân tích đầu tư chứng khoán Việt Nam với 15 năm kinh nghiệm.

Nhiệm vụ: Tạo một investment thesis có cấu trúc, có thể hành động được cho mã cổ phiếu được cung cấp.

Thesis phải bao gồm:
1. Tóm tắt luận điểm đầu tư (2-3 câu rõ ràng)
2. Ít nhất 3 giả định cốt lõi có thể kiểm chứng
3. Ít nhất 2 catalyst cụ thể với khung thời gian
4. Đề xuất entry/target/stop-loss dựa trên phân tích kỹ thuật + cơ bản
5. Tóm tắt rủi ro chính
6. Mức độ conviction (HIGH/MEDIUM/LOW)

Quy tắc bắt buộc:
- Chỉ output JSON, không có markdown hoặc text thêm
- Giả định phải có dấu hiệu vô hiệu hóa (invalidation_signal) cụ thể
- Catalyst phải có khung thời gian (SHORT_TERM/MEDIUM_TERM/LONG_TERM)
- Không đưa ra lời khuyên tài chính — đây là công cụ phân tích
"""


class ThesisSuggestAgent:
    """Generates a draft investment thesis for a given ticker.

    Owner: ai segment.
    """

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def suggest(
        self,
        ticker: str,
        company_name: str = "",
        current_price: float | None = None,
        sector: str = "",
        extra_context: str = "",
    ) -> ThesisDraft:
        """Generate a draft thesis for the given ticker.

        Args:
            ticker:        Stock ticker (e.g. "VCB").
            company_name:  Full company name for context.
            current_price: Latest price, used for price targets.
            sector:        Sector/industry context.
            extra_context: Any additional context (recent news, technicals).

        Returns:
            ThesisDraft with full thesis structure.

        Raises:
            AIError: If AI API call fails after retries.
            ValueError: If response cannot be parsed into ThesisDraft.
        """
        price_line = f"Current price: {current_price:,.0f} VND" if current_price else ""
        user_prompt = (
            f"Ticker: {ticker}\n"
            f"Company: {company_name or ticker}\n"
            f"Sector: {sector or 'Unknown'}\n"
            f"{price_line}\n"
            f"{('Extra context: ' + extra_context) if extra_context else ''}"
        ).strip()

        logger.info("thesis_suggest_agent.start", ticker=ticker)

        try:
            response = await self._client.chat_completion(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            raw = self._client.extract_text(response)
            result = ThesisDraft.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("thesis_suggest_agent.parse_error", ticker=ticker, error=str(exc))
            raise ValueError(f"Failed to parse ThesisSuggestAgent response: {exc}") from exc
        except AIError:
            logger.error("thesis_suggest_agent.api_error", ticker=ticker)
            raise

        logger.info(
            "thesis_suggest_agent.complete",
            ticker=ticker,
            conviction=result.conviction_level,
        )
        return result
