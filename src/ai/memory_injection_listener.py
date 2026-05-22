"""MemoryInjectionListener — write post-mortem lessons to long-term memory.

Owner: ai segment.

Responsibilities:
  - Subscribe to ThesisPostMortemReadyEvent
  - Format structured lesson as a memory entry (plain text, tagged)
  - Append to MemoryService for the investor's user_id

Non-responsibilities:
  - Does NOT call AI (data already extracted by PostMortemService)
  - Does NOT send Discord messages (that is bot.PostMortemSubscriber)
  - Does NOT own the memory store schema

Memory format injected::

    [PostMortem][VCB][INCORRECT][premature_entry] 2026-05-23
    Thesis: "VCB breakout play Q2 2026"
    Lesson: Vào lệnh quá sớm trước khi catalyst xác nhận; volume chưa đủ.
    P&L: -4.2% | Tags: VCB, premature_entry, breakout, volume_confirm
"""

from __future__ import annotations

import datetime

from src.ai.memory.memory_service import MemoryService
from src.platform.db import AsyncSessionLocal
from src.platform.event_bus import get_event_bus
from src.platform.events import ThesisPostMortemReadyEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)


class MemoryInjectionListener:
    """Subscribe ThesisPostMortemReadyEvent → write to MemoryService.

    session_factory injected for testability; defaults to AsyncSessionLocal.
    """

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or AsyncSessionLocal

    def register(self) -> None:
        bus = get_event_bus()
        bus.subscribe(ThesisPostMortemReadyEvent, self._handle)
        logger.info("memory_injection_listener.registered")

    async def _handle(self, event: ThesisPostMortemReadyEvent) -> None:
        entry = _format_memory_entry(event)
        try:
            async with self._session_factory() as session:
                svc = MemoryService(session)
                await svc.append(
                    user_id=event.user_id,
                    content=entry,
                    tags=list(event.memory_tags),
                    source="post_mortem",
                )
            logger.info(
                "memory_injection_listener.written",
                thesis_id=event.thesis_id,
                ticker=event.ticker,
                verdict=event.verdict,
                tags=list(event.memory_tags),
            )
        except Exception as exc:
            logger.error(
                "memory_injection_listener.failed",
                thesis_id=event.thesis_id,
                ticker=event.ticker,
                error=str(exc),
            )


def _format_memory_entry(event: ThesisPostMortemReadyEvent) -> str:
    """Render a structured plain-text memory entry from the post-mortem event."""
    date_str = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%d")
    pnl_str = (
        f"{event.outcome_pnl_pct:+.1f}%"
        if event.outcome_pnl_pct is not None
        else "N/A"
    )
    tags_str = ", ".join(event.memory_tags) if event.memory_tags else ""
    lines = [
        f"[PostMortem][{event.ticker}][{event.verdict}][{event.pattern}] {date_str}",
        f'Thesis: "{event.thesis_title}"',
        f"Reason: {event.close_reason}",
        f"Lesson: {event.lesson}",
        f"P&L: {pnl_str}",
    ]
    if tags_str:
        lines.append(f"Tags: {tags_str}")
    return "\n".join(lines)
