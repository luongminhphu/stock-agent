"""FeedbackLedgerSubscriber — gom mọi phản hồi của nhà đầu tư vào ``user_behavior_logs``.

Owner: ai.memory (Wave E3a — S6 feedback ledger hợp nhất).

Trước E3a có 3 stream không giao nhau:
  core_feedback      ← EngineFeedbackSubmittedEvent (verdict core)   → chỉ core.evolution đọc
  brief_feedback     ← BriefingService.record_feedback              → readmodel/dashboard đọc
  user_behavior_logs ← UserActionEvent (bought/sold/ignored...)      → ai.memory.lesson đọc

Subscriber này dual-write thêm 1 row ledger cho mỗi feedback core/brief/pretrade, với
``ref_type``/``ref_id`` trỏ về đối tượng gốc. Hai bảng cũ không đổi (reader cũ giữ
nguyên); readmodel.AccuracyProjection (E3c) đọc ledger làm 1 nguồn.

Ledger là best-effort: lỗi ghi chỉ log, không raise (event gốc đã được xử lý).
"""

from __future__ import annotations

from src.platform.db import get_session
from src.platform.event_bus import EventBus, get_event_bus
from src.platform.events import (
    BriefFeedbackRecordedEvent,
    EngineFeedbackSubmittedEvent,
    PretradeAdviceReconciledEvent,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

SOURCE_CORE = "core"
SOURCE_BRIEFING = "briefing"
SOURCE_THESIS = "thesis"

REF_VERDICT = "verdict"
REF_BRIEF = "brief"
REF_PRETRADE = "pretrade"


class FeedbackLedgerSubscriber:
    def __init__(self, bus: EventBus | None = None) -> None:
        self._bus = bus or get_event_bus()

    def register(self) -> None:
        self._bus.subscribe_handler(EngineFeedbackSubmittedEvent, self._on_engine_feedback)
        self._bus.subscribe_handler(BriefFeedbackRecordedEvent, self._on_brief_feedback)
        self._bus.subscribe_handler(PretradeAdviceReconciledEvent, self._on_pretrade_reconciled)
        logger.info("feedback_ledger.registered")

    # ── handlers ────────────────────────────────────────────────────────────

    async def _on_engine_feedback(self, event: EngineFeedbackSubmittedEvent) -> None:
        await self._write(
            user_id=event.user_id,
            signal=f"engine:{event.outcome}",
            source=SOURCE_CORE,
            ref_type=REF_VERDICT,
            ref_id=event.verdict_event_id,
            agent_type="intelligence_engine",
            note=(event.user_note or None),
        )

    async def _on_brief_feedback(self, event: BriefFeedbackRecordedEvent) -> None:
        await self._write(
            user_id=event.user_id,
            signal=f"brief:{event.outcome}",
            source=SOURCE_BRIEFING,
            ref_type=REF_BRIEF,
            ref_id=str(event.brief_snapshot_id),
            agent_type=f"briefing:{event.brief_type}" if event.brief_type else "briefing",
        )

    async def _on_pretrade_reconciled(self, event: PretradeAdviceReconciledEvent) -> None:
        await self._write(
            user_id=event.user_id,
            signal=event.adherence,
            source=SOURCE_THESIS,
            ref_type=REF_PRETRADE,
            ref_id=str(event.decision_log_id),
            ticker=event.ticker,
            agent_type="pretrade",
            note=f"advice={event.advice_verdict} action={event.action_type}",
        )

    # ── write ───────────────────────────────────────────────────────────────

    async def _write(
        self,
        *,
        user_id: str,
        signal: str,
        source: str,
        ref_type: str,
        ref_id: str,
        ticker: str | None = None,
        agent_type: str | None = None,
        note: str | None = None,
    ) -> None:
        from src.ai.memory.user_behavior_log import UserBehaviorLog

        if not user_id:
            logger.warning("feedback_ledger.skip_no_user", ref_type=ref_type, ref_id=ref_id)
            return
        try:
            async with get_session() as session:
                session.add(
                    UserBehaviorLog(
                        user_id=user_id,
                        signal=signal[:32],
                        source=source,
                        ticker=ticker.upper() if ticker else None,
                        agent_type=agent_type,
                        note=note[:512] if note else None,
                        ref_type=ref_type,
                        ref_id=ref_id[:64],
                    )
                )
            logger.info(
                "feedback_ledger.recorded",
                source=source,
                signal=signal,
                ref_type=ref_type,
                ref_id=ref_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "feedback_ledger.write_failed",
                source=source,
                ref_type=ref_type,
                ref_id=ref_id,
                error=str(exc),
            )
