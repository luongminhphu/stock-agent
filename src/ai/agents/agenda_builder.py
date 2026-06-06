"""AgendaBuilderAgent — tạo daily investor agenda từ decision + thesis + memory data.

Owner: ai segment.
Consumed by: briefing.AgendaService.

Boundary:
- Nhận AgendaContext, trả DailyAgendaResult.
- Không đọc DB, không gọi service, không gửi notification.
"""
from __future__ import annotations


from src.ai.client import AIClient
from src.ai.prompts.agenda import (
    AgendaContext,
    DailyAgendaResult,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)


class AgendaBuilderAgent:
    """Build a DailyAgendaResult from an AgendaContext.

    Returns None on any AI or parse failure so callers can degrade gracefully.
    """

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def build(self, ctx: AgendaContext) -> DailyAgendaResult | None:
        """Build daily agenda. Returns None if AI call fails — caller handles fallback."""
        try:
            result = await self._client.chat(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(ctx),
                response_schema=DailyAgendaResult,
                temperature=0.3,
            )
            logger.info(
                "agenda_builder.built",
                decide_count=len(result.decide),
                watch_count=len(result.watch),
                defer_count=len(result.defer),
            )
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("agenda_builder.failed", error=str(exc))
            return None
