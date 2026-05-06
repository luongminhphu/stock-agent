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

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.ai.client import AIClient, AIError
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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

Output PHẢI theo đúng JSON schema sau — field names phải khớp chính xác:
{
  "ticker": "VCB",
  "title": "Tiêu đề thesis ngắn gọn",
  "summary": "Tóm tắt luận điểm 2-3 câu",
  "entry_price_suggestion": 85000,
  "target_price_suggestion": 105000,
  "stop_loss_suggestion": 78000,
  "time_horizon": "MEDIUM_TERM",
  "assumptions": [
    {
      "description": "Mô tả giả định cụ thể",
      "invalidation_signal": "Dấu hiệu vô hiệu hóa giả định"
    }
  ],
  "catalysts": [
    {
      "description": "Mô tả catalyst cụ thể",
      "expected_timeframe": "SHORT_TERM"
    }
  ],
  "risk_summary": "Tóm tắt rủi ro chính",
  "conviction_level": "MEDIUM"
}
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
        session: AsyncSession | None = None,
        user_id: str | None = None,
        trigger: str = "suggest",
    ) -> ThesisDraft:
        """Generate a draft thesis for the given ticker.

        Args:
            ticker:        Stock ticker (e.g. "VCB").
            company_name:  Full company name for context.
            current_price: Latest price, used for price targets.
            sector:        Sector/industry context.
            extra_context: Any additional context (recent news, technicals).
            session:       Optional AsyncSession. When provided, investor profile +
                           memory context are injected into the prompt.
            user_id:       Optional user_id for memory logging.
            trigger:       Trigger label for episodic log.

        Returns:
            ThesisDraft with full thesis structure.

        Raises:
            AIError: If AI API call fails after retries.
            ValueError: If response cannot be parsed into ThesisDraft.
        """
        investor_profile = await self._build_investor_profile(session, user_id)
        price_line = f"Current price: {current_price:,.0f} VND" if current_price else ""
        user_prompt = (
            f"Ticker: {ticker}\n"
            f"Company: {company_name or ticker}\n"
            f"Sector: {sector or 'Unknown'}\n"
            f"{price_line}\n"
            f"{('Extra context: ' + extra_context) if extra_context else ''}"
        ).strip()

        if investor_profile:
            user_prompt += f"\n\n{investor_profile}"

        logger.info("thesis_suggest_agent.start", ticker=ticker)

        try:
            # client.chat() enforces JSON via prompt, strips fences, and parses
            # into the Pydantic schema — no manual json.loads() needed.
            result = await self._client.chat(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=ThesisDraft,
                temperature=0.3,
            )
        except AIError as exc:
            logger.error("thesis_suggest_agent.api_error", ticker=ticker, error=str(exc))
            raise

        logger.info(
            "thesis_suggest_agent.complete",
            ticker=ticker,
            conviction=result.conviction_level,
        )

        # --- Memory: log interaction (Layer 2) ---
        await self._log_interaction(
            session=session,
            user_id=user_id,
            ticker=ticker,
            result=result,
            trigger=trigger,
        )

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _build_investor_profile(self, session, user_id: str | None) -> str:
        """Build investor profile block via ContextBuilder (includes memory).

        Returns empty string when session is None or any error occurs.
        Owner of assembly logic: ai.ContextBuilder.
        """
        if session is None:
            return ""
        try:
            from src.ai.context_builder import ContextBuilder, render_for_agent

            ctx = await ContextBuilder(session).build(user_id=user_id)
            return render_for_agent(ctx)
        except Exception as exc:
            logger.warning("suggest_agent.investor_profile_failed", error=str(exc))
            return ""

    async def _log_interaction(
        self,
        session,
        user_id: str | None,
        ticker: str,
        result: ThesisDraft,
        trigger: str,
    ) -> None:
        """Fire-and-forget memory log. Never raises."""
        if session is None or not user_id:
            return
        try:
            from src.ai.memory.memory_service import InteractionEntry, MemoryService

            entry = InteractionEntry(
                user_id=user_id,
                agent_type="suggest",
                trigger=trigger,
                tickers=[ticker],
                ai_verdict=result.conviction_level,
                ai_confidence=None,
                ai_key_points=result.summary[:300] if result.summary else None,
                ai_risk_signals=result.risk_summary[:300] if result.risk_summary else None,
            )
            await MemoryService.log_interaction(session, entry)
        except Exception as exc:
            logger.warning(
                "suggest_agent.memory_log_failed",
                ticker=ticker,
                error=str(exc),
            )
