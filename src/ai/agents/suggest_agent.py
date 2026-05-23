"""ThesisSuggestAgent — generate a draft investment thesis for a given ticker.

Owner: ai segment.
Callers:
    - bot/commands/suggest (Discord /suggest command)
    - api routes (future)

Boundary:
    - Accepts ticker string + optional context strings.
    - Returns ThesisSuggestionResult (public ai.schemas contract).
    - Does NOT write DB, does NOT call thesis/watchlist repositories.
    - Caller (bot or service) is responsible for persisting the draft if desired.

Note on schema:
    ThesisDraft is the internal parse target (matches AI JSON output).
    ThesisSuggestionResult is the public contract returned to callers.
    Mapping happens here — callers always see ThesisSuggestionResult.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.ai.client import AIClient, AIError
from src.ai.schemas import SuggestedAssumption, SuggestedCatalyst, ThesisSuggestionResult
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# sonar-pro has been observed cutting output around 1154 tokens when left to default.
# 3000 gives enough headroom for a full thesis JSON (~800-1200 tokens with constraints).
_MAX_TOKENS = 3000


class AssumptionDraft(BaseModel):
    description: str
    rationale: str = Field(
        ..., description="Tại sao đây là giả định then chốt của thesis"
    )
    invalidation_signal: str


class CatalystDraft(BaseModel):
    description: str
    expected_timeline: str = Field(
        ...,
        description=(
            "Timeline cụ thể khi catalyst dự kiến xảy ra. "
            "PHẢI là chuỗi có năm cụ thể, VD: 'Q3 2026', 'H1 2027', 'tháng 6 2026', 'cuối năm 2026'. "
            "KHÔNG dùng 'SHORT_TERM', 'MEDIUM_TERM', 'LONG_TERM'."
        ),
    )
    rationale: str = Field(
        ..., description="Tại sao catalyst này có thể thúc đẩy giá"
    )


class ThesisDraft(BaseModel):
    """Structured thesis draft produced by the AI.

    Owner: ai segment (internal parse target only).
    Mapped to ThesisSuggestionResult before returning to callers.
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
    reasoning: str = Field(
        ..., description="Lý do tổng thể AI đề xuất thesis này: luận điểm chính, điểm mạnh của cổ phiếu, bối cảnh đầu tư"
    )
    conviction_level: str = Field(
        ..., description="HIGH | MEDIUM | LOW"
    )


_SYSTEM_PROMPT = """
NGÔN NGỮ BẮT BUỘC: Toàn bộ nội dung text (title, summary, description, rationale, invalidation_signal, risk_summary, reasoning) phải viết bằng TIẾNG VIỆT. Chỉ giữ tiếng Anh cho: ticker, các giá trị enum (SHORT_TERM, MEDIUM_TERM, LONG_TERM, HIGH, MEDIUM, LOW) — nhưng KHÔNG dùng enum cho expected_timeline (xem quy tắc bên dưới).

GIỚI HẠN ĐỘ DÀI (bắt buộc để tránh output bị cắt):
- title: tối đa 15 từ
- summary: tối đa 60 từ (2-3 câu ngắn gọn)
- mỗi description, rationale, invalidation_signal: tối đa 40 từ
- risk_summary: tối đa 60 từ
- reasoning: tối đa 80 từ

Bạn là chuyên gia phân tích đầu tư chứng khoán Việt Nam với 15 năm kinh nghiệm.

Nhiệm vụ: Tạo một investment thesis có cấu trúc, có thể hành động được cho mã cổ phiếu được cung cấp.

Thesis phải bao gồm:
1. Tóm tắt luận điểm đầu tư (2-3 câu, viết bằng tiếng Việt, tối đa 60 từ)
2. Ít nhất 3 giả định cốt lõi — mỗi giả định: description + rationale + invalidation_signal (viết bằng tiếng Việt, mỗi field tối đa 40 từ)
3. Ít nhất 2 catalyst — mỗi catalyst: description + expected_timeline + rationale (viết bằng tiếng Việt, mỗi field tối đa 40 từ)
4. Đề xuất entry/target/stop-loss (số)
5. risk_summary: tối đa 60 từ, tiếng Việt
6. reasoning: tối đa 80 từ, tiếng Việt
7. conviction_level: HIGH | MEDIUM | LOW

Quy tắc bắt buộc:
- Chỉ output JSON, không có markdown hoặc text thêm
- Không đưa ra lời khuyên tài chính — đây là công cụ phân tích
- Tuân thủ giới hạn độ dài để JSON không bị cắt
- expected_timeline PHẢI là chuỗi có năm cụ thể, VD: "Q3 2026", "H1 2027", "tháng 6 2026", "cuối năm 2026"
- TUYỆT ĐỐI KHÔNG dùng "SHORT_TERM", "MEDIUM_TERM", "LONG_TERM" cho expected_timeline

Output PHẢI theo đúng JSON schema sau — field names phải khớp chính xác:
{
  "ticker": "VCB",
  "title": "Tiêu đề thesis ngắn gọn bằng tiếng Việt",
  "summary": "Tóm tắt luận điểm 2-3 câu bằng tiếng Việt",
  "entry_price_suggestion": 85000,
  "target_price_suggestion": 105000,
  "stop_loss_suggestion": 78000,
  "time_horizon": "MEDIUM_TERM",
  "assumptions": [
    {
      "description": "Mô tả giả định cụ thể bằng tiếng Việt",
      "rationale": "Tại sao đây là giả định then chốt của thesis",
      "invalidation_signal": "Dấu hiệu vô hiệu hóa giả định"
    }
  ],
  "catalysts": [
    {
      "description": "Mô tả catalyst cụ thể bằng tiếng Việt",
      "expected_timeline": "Q3 2026",
      "rationale": "Tại sao catalyst này có thể thúc đẩy giá tăng"
    }
  ],
  "risk_summary": "Tóm tắt rủi ro chính bằng tiếng Việt",
  "reasoning": "Lý do tổng thể vì sao AI đề xuất thesis này bằng tiếng Việt",
  "conviction_level": "MEDIUM"
}
"""


def _conviction_to_confidence(level: str) -> float:
    """Map conviction level string to float confidence score."""
    return {"HIGH": 0.85, "MEDIUM": 0.60, "LOW": 0.35}.get(level.upper(), 0.50)


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
    ) -> ThesisSuggestionResult:
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
            ThesisSuggestionResult with full thesis structure.

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
            draft = await self._client.chat(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=ThesisDraft,
                temperature=0.3,
                max_tokens=_MAX_TOKENS,
            )
        except AIError as exc:
            logger.error("thesis_suggest_agent.api_error", ticker=ticker, error=str(exc))
            raise

        logger.info(
            "thesis_suggest_agent.complete",
            ticker=ticker,
            conviction=draft.conviction_level,
        )

        # --- Map ThesisDraft (internal) → ThesisSuggestionResult (public contract) ---
        result = ThesisSuggestionResult(
            ticker=draft.ticker,
            thesis_title=draft.title,
            thesis_summary=draft.summary,
            entry_price_hint=draft.entry_price_suggestion,
            target_price_hint=draft.target_price_suggestion,
            stop_loss_hint=draft.stop_loss_suggestion,
            assumptions=[
                SuggestedAssumption(
                    description=a.description,
                    rationale=a.rationale,
                )
                for a in draft.assumptions
            ],
            catalysts=[
                SuggestedCatalyst(
                    description=c.description,
                    expected_timeline=c.expected_timeline,
                    rationale=c.rationale,
                )
                for c in draft.catalysts
            ],
            confidence=_conviction_to_confidence(draft.conviction_level),
            reasoning=draft.reasoning,
        )

        # --- Memory: log interaction (Layer 2) ---
        await self._log_interaction(
            session=session,
            user_id=user_id,
            ticker=ticker,
            result=draft,
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
