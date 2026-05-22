"""PostMortemService — AI-driven lesson extraction when a thesis closes.

Owner: thesis segment.

Responsibilities:
  - Subscribe to ThesisClosedEvent (emitted by ThesisService.close / .invalidate)
  - Call AI to extract structured lesson: what worked, what failed, pattern label
  - Emit ThesisPostMortemReadyEvent for downstream consumers
    (ai.MemoryInjectionListener, bot.PostMortemSubscriber)

Non-responsibilities:
  - Does NOT write to DB — lesson persistence is done by MemoryInjectionListener
  - Does NOT send Discord messages — that is bot.PostMortemSubscriber
  - Does NOT modify the thesis record

AI prompt contract:
  Input:  thesis metadata + DecisionLog lessons (last 3 relevant entries)
  Output: PostMortemOutput (lesson, pattern, verdict, confidence, memory_tags)
"""

from __future__ import annotations

from dataclasses import dataclass

from src.ai.client import AIClient
from src.ai.schemas import PostMortemOutput
from src.platform.db import AsyncSessionLocal
from src.platform.event_bus import get_event_bus
from src.platform.events import ThesisClosedEvent, ThesisPostMortemReadyEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)


class PostMortemService:
    """Subscribe ThesisClosedEvent → AI extraction → emit ThesisPostMortemReadyEvent.

    Instantiated as a singleton at bootstrap. session_factory is used per-call
    to avoid holding a long-lived session across the event bus queue.
    """

    def __init__(self, ai_client: AIClient, session_factory=None) -> None:
        self._ai = ai_client
        self._session_factory = session_factory or AsyncSessionLocal

    def register(self) -> None:
        bus = get_event_bus()
        bus.subscribe(ThesisClosedEvent, self._handle)
        logger.info("post_mortem_service.registered")

    async def _handle(self, event: ThesisClosedEvent) -> None:
        logger.info(
            "post_mortem_service.started",
            thesis_id=event.thesis_id,
            ticker=event.ticker,
            close_reason=event.close_reason,
        )
        try:
            lesson_context = await self._build_lesson_context(event)
            output = await self._run_ai(event, lesson_context)
            await self._emit(event, output)
        except Exception as exc:
            logger.error(
                "post_mortem_service.failed",
                thesis_id=event.thesis_id,
                ticker=event.ticker,
                error=str(exc),
            )
            # Fail silently — post-mortem is non-blocking for the investor

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _build_lesson_context(self, event: ThesisClosedEvent) -> str:
        """Pull recent DecisionLog lessons to ground the AI prompt."""
        try:
            async with self._session_factory() as session:
                from src.thesis.lesson_service import LessonService
                svc = LessonService(session)
                return await svc.build_lesson_context(
                    event.user_id,
                    ticker=event.ticker,
                    limit=3,
                    lookback_days=180,
                )
        except Exception as exc:
            logger.warning(
                "post_mortem_service.lesson_context_failed",
                error=str(exc),
            )
            return ""

    async def _run_ai(self, event: ThesisClosedEvent, lesson_context: str) -> "PostMortemOutput":
        pnl_str = (
            f"{event.outcome_pnl_pct:+.1f}%"
            if event.outcome_pnl_pct is not None
            else "not yet available"
        )
        prompt = _build_prompt(
            ticker=event.ticker,
            title=event.thesis_title,
            summary=event.thesis_summary,
            close_reason=event.close_reason,
            pnl_str=pnl_str,
            lesson_context=lesson_context,
        )
        return await self._ai.structured_completion(
            prompt=prompt,
            schema=PostMortemOutput,
            system_prompt=_SYSTEM_PROMPT,
        )

    async def _emit(self, event: ThesisClosedEvent, output: "PostMortemOutput") -> None:
        result = ThesisPostMortemReadyEvent(
            thesis_id=event.thesis_id,
            user_id=event.user_id,
            ticker=event.ticker,
            close_reason=event.close_reason,
            thesis_title=event.thesis_title,
            lesson=output.lesson,
            pattern=output.pattern,
            verdict=output.verdict,
            confidence=output.confidence,
            outcome_pnl_pct=event.outcome_pnl_pct,
            memory_tags=tuple(output.memory_tags),
        )
        bus = get_event_bus()
        await bus.publish(result)
        logger.info(
            "post_mortem_service.emitted",
            thesis_id=event.thesis_id,
            ticker=event.ticker,
            verdict=output.verdict,
            pattern=output.pattern,
        )


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Bạn là AI phân tích đầu tư chứng khoán Việt Nam. Nhiệm vụ của bạn là thực hiện post-mortem
một thesis đầu tư vừa đóng, rút ra bài học cụ thể và gắn nhãn pattern hành vi nhà đầu tư.

Yêu cầu đầu ra:
- lesson: 1-2 câu súc tích, rõ nguyên nhân thành công/thất bại
- pattern: 1 nhãn ngắn gọn (snake_case), ví dụ: premature_entry, thesis_drift, catalyst_miss,
  correct_breakout, stop_loss_discipline, overconfidence, position_sizing_error
- verdict: CORRECT | INCORRECT | MIXED | INCONCLUSIVE
- confidence: 0.0-1.0
- memory_tags: 3-5 từ khoá ngắn để index memory (ticker + pattern + market context)
"""


def _build_prompt(
    ticker: str,
    title: str,
    summary: str,
    close_reason: str,
    pnl_str: str,
    lesson_context: str,
) -> str:
    parts = [
        f"Ticker: {ticker}",
        f"Thesis: {title}",
        f"Summary: {summary}",
        f"Close reason: {close_reason}",
        f"P&L: {pnl_str}",
    ]
    if lesson_context:
        parts.append(f"\nBài học lịch sử liên quan:\n{lesson_context}")
    parts.append("\nHãy thực hiện post-mortem và trả về structured output.")
    return "\n".join(parts)
