"""
SignalReviewTriggerListener — Thesis Segment

Subscribes to ThesisReviewTriggeredEvent emitted by ai.SignalEngineListener.
Resolves thesis from DB (by thesis_id or ticker fallback), then enqueues
ThesisJudgeAgent for each trigger that passes dedup + rate-limit guard.

Owner: thesis segment. Adapter only — no AI logic here.
AI logic lives in ai/agents/thesis_judge.py (not modified by this patch).

Boundary:
  - KHÔNG gọi SignalEngineAgent trực tiếp.
  - KHÔNG chứa thesis domain rule (valid/invalid/scoring).
  - KHÔNG emit ThesisReviewRequestedEvent — chỉ gọi judge service trực tiếp.
  - bot và api không gọi listener này trực tiếp.

Wire-up: call SignalReviewTriggerListener(...).register() during app bootstrap,
after ThesisJudgeService is ready.

Dedup strategy:
  In-memory set of (thesis_id, phase) pairs per process lifetime.
  Prevents the same thesis being judged twice in one signal engine run
  (e.g. two conflicting signals both triggering a review for VCB).
  Set is cleared on each new SignalEngineCompletedEvent to allow
  re-triggering across different runs.

Rate-limit guard:
  If thesis_judge_service exposes `get_last_judge_run_at(thesis_id)`,
  skip re-judging if last run was < DEDUP_WINDOW_MINUTES ago.
  Falls back to in-memory dedup only if method not available.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.platform.event_bus import get_event_bus
from src.platform.events import (
    SignalEngineCompletedEvent,
    ThesisReviewTriggeredEvent,
)
from src.platform.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# Re-judge guard: skip if last judge run was within this window.
# Prevents thrashing when multiple CRITICAL signals fire for same thesis.
DEDUP_WINDOW_MINUTES: int = 30


class SignalReviewTriggerListener:
    """Listens for ThesisReviewTriggeredEvent, enqueues ThesisJudgeAgent.

    Lifecycle::

        listener = SignalReviewTriggerListener(
            thesis_query=thesis_query,
            thesis_judge_service=thesis_judge_service,
        )
        listener.register()  # call once in on_ready, after bootstrap

    thesis_query must expose:
        get_by_id(thesis_id: str) -> dict | None
        get_active_by_ticker(ticker: str, user_id: str) -> list[dict]

    thesis_judge_service must expose:
        enqueue_judge(thesis: dict, reason: str, urgency: str) -> None
        get_last_judge_run_at(thesis_id: str) -> datetime | None  # optional
    """

    def __init__(
        self,
        thesis_query: Any,
        thesis_judge_service: Any,
    ) -> None:
        self._thesis = thesis_query
        self._judge = thesis_judge_service
        self._registered = False
        # Dedup: cleared on each new SignalEngineCompletedEvent
        self._seen_this_run: set[tuple[str, str]] = set()

    def register(self) -> None:
        """Subscribe to both ThesisReviewTriggeredEvent and SignalEngineCompletedEvent.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._registered:
            logger.warning("SignalReviewTriggerListener already registered — skipping.")
            return
        bus = get_event_bus()
        bus.subscribe_handler(ThesisReviewTriggeredEvent, self._handle_trigger)
        bus.subscribe_handler(SignalEngineCompletedEvent, self._reset_dedup)
        self._registered = True
        logger.info("SignalReviewTriggerListener registered on event bus.")

    # ── internal ───────────────────────────────────────────────────────────

    async def _reset_dedup(self, _event: SignalEngineCompletedEvent) -> None:
        """Clear per-run dedup set at the start of each new signal engine run.

        Note: SignalEngineCompletedEvent fires *after* all ThesisReviewTriggeredEvents
        from the same run. Dedup is therefore effective within a single run.
        Across runs, the set is cleared so re-triggering works correctly.
        """
        self._seen_this_run.clear()

    async def _handle_trigger(self, event: ThesisReviewTriggeredEvent) -> None:
        """Handle one ThesisReviewTriggeredEvent.

        1. Resolve thesis: by thesis_id if non-empty, else by ticker+user_id.
        2. Dedup guard: skip if (thesis_id, phase) already seen this run.
        3. Rate-limit guard: skip if last judge run < DEDUP_WINDOW_MINUTES ago.
        4. Enqueue ThesisJudgeAgent via thesis_judge_service.
        """
        logger.info(
            "signal_review_trigger_listener.received",
            thesis_id=event.thesis_id,
            ticker=event.ticker,
            urgency=event.urgency,
            phase=event.phase,
        )

        # -- 1. Resolve thesis -------------------------------------------
        thesis = await self._resolve_thesis(event)
        if thesis is None:
            logger.warning(
                "signal_review_trigger_listener.thesis_not_found",
                thesis_id=event.thesis_id,
                ticker=event.ticker,
                user_id=event.user_id,
            )
            return

        resolved_id = str(thesis.get("id", event.thesis_id))

        # -- 2. Dedup guard ----------------------------------------------
        dedup_key = (resolved_id, event.phase)
        if dedup_key in self._seen_this_run:
            logger.debug(
                "signal_review_trigger_listener.dedup_skip",
                thesis_id=resolved_id,
                phase=event.phase,
            )
            return
        self._seen_this_run.add(dedup_key)

        # -- 3. Rate-limit guard (optional) ------------------------------
        if await self._is_recently_judged(resolved_id):
            logger.info(
                "signal_review_trigger_listener.rate_limit_skip",
                thesis_id=resolved_id,
                window_minutes=DEDUP_WINDOW_MINUTES,
            )
            return

        # -- 4. Enqueue judge -------------------------------------------
        try:
            await self._judge.enqueue_judge(
                thesis=thesis,
                reason=event.reason,
                urgency=event.urgency,
            )
            logger.info(
                "signal_review_trigger_listener.enqueued",
                thesis_id=resolved_id,
                ticker=event.ticker,
                urgency=event.urgency,
            )
        except Exception as exc:
            logger.exception(
                "signal_review_trigger_listener.enqueue_failed",
                thesis_id=resolved_id,
                error=str(exc),
            )

    async def _resolve_thesis(self, event: ThesisReviewTriggeredEvent) -> dict[str, Any] | None:
        """Resolve thesis dict from DB.

        Primary: thesis_id (non-empty) → direct lookup.
        Fallback: ticker + user_id → get first active thesis for that ticker.
        Returns None if neither path yields a result.
        """
        if event.thesis_id:
            try:
                return await self._thesis.get_by_id(event.thesis_id)
            except Exception as exc:
                logger.warning(
                    "signal_review_trigger_listener.get_by_id_failed",
                    thesis_id=event.thesis_id,
                    error=str(exc),
                )

        # Ticker fallback (used in fallback mode where thesis_id is empty)
        if event.ticker and event.user_id:
            try:
                theses = await self._thesis.get_active_by_ticker(
                    ticker=event.ticker,
                    user_id=event.user_id,
                )
                return theses[0] if theses else None
            except Exception as exc:
                logger.warning(
                    "signal_review_trigger_listener.ticker_fallback_failed",
                    ticker=event.ticker,
                    error=str(exc),
                )

        return None

    async def _is_recently_judged(self, thesis_id: str) -> bool:
        """Check if this thesis was judged recently (within DEDUP_WINDOW_MINUTES).

        Returns False if judge service doesn't support get_last_judge_run_at,
        or if the call fails — err on the side of allowing the judge to run.
        """
        if not hasattr(self._judge, "get_last_judge_run_at"):
            return False
        try:
            from datetime import UTC, datetime, timedelta
            last_run = await self._judge.get_last_judge_run_at(thesis_id)
            if last_run is None:
                return False
            return datetime.now(UTC) - last_run < timedelta(minutes=DEDUP_WINDOW_MINUTES)
        except Exception:
            return False
