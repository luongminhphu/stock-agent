"""
IntelligenceEngineListener — EventBus subscriber wiring the core engine.

Owner: core segment.

Consumed event : IntelligenceEngineRequestedEvent
Emitted event  : IntelligenceEngineCompletedEvent
Side-effect    : None. Discord delivery is handled by
                 src.bot.intelligence_engine_subscriber.IntelligenceEngineSubscriber,
                 which subscribes to IntelligenceEngineCompletedEvent.

Wave 3 change:
    Passes event.signal_engine_summary into engine.run_cycle() so the
    AI verdict prompt receives richer cross-segment context.
    verdict_event_id echoed on IntelligenceEngineCompletedEvent so
    downstream feedback submissions can reference it.

Boot: call IntelligenceEngineListener(...).register() in platform bootstrap.
"""
from __future__ import annotations

import uuid
from typing import Any

from src.core import engine
from src.platform.event_bus import EventBus, get_event_bus
from src.platform.events import (
    IntelligenceEngineCompletedEvent,
    IntelligenceEngineRequestedEvent,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)


class IntelligenceEngineListener:
    """Subscribe to IntelligenceEngineRequestedEvent → run engine cycle → emit CompletedEvent.

    This class is owned by the *core* segment and must not import from src.bot.*.
    All Discord/notification side-effects are handled by downstream subscribers
    of IntelligenceEngineCompletedEvent (e.g. IntelligenceEngineSubscriber in bot).

    Args:
        bus:          EventBus instance. Defaults to get_event_bus() singleton.
        verdict_agent: Optional IntelligenceVerdictAgent (ai segment).
                       When provided, Wave 2 AI synthesis is active.
                       When None (default), Wave 1 heuristic runs only.
    """

    def __init__(
        self,
        bus: EventBus | None = None,
        verdict_agent: Any | None = None,
    ) -> None:
        self._bus = bus or get_event_bus()
        self._verdict_agent = verdict_agent

    def register(self) -> None:
        self._bus.subscribe(IntelligenceEngineRequestedEvent, self._handle)
        logger.info(
            "intelligence_listener.registered",
            wave="2_ai" if self._verdict_agent is not None else "1_heuristic",
        )

    async def _handle(self, event: IntelligenceEngineRequestedEvent) -> None:
        logger.info(
            "intelligence_listener.received",
            trigger_source=event.trigger_source,
            priority=event.priority,
            user_id=event.user_id,
            has_signal_summary=bool(event.signal_engine_summary),
        )

        verdict = await engine.run_cycle(
            user_id=event.user_id,
            trigger_source=event.trigger_source,
            priority=event.priority,
            context_hint=event.context_hint,
            signal_engine_summary=event.signal_engine_summary,
            verdict_agent=self._verdict_agent,
        )

        if verdict is None:
            logger.info(
                "intelligence_listener.no_verdict",
                trigger_source=event.trigger_source,
                reason="below_threshold_or_snapshot_failed",
            )
            return

        # ── Emit IntelligenceEngineCompletedEvent ──────────────────────────
        echoed_verdict_event_id: str = (
            getattr(verdict, "verdict_event_id", None) or str(uuid.uuid4())
        )

        def _to_tuple(val: Any) -> tuple[str, ...]:
            if not val:
                return ()
            return tuple(str(v) for v in val)

        completed = IntelligenceEngineCompletedEvent(
            verdict=verdict.verdict,
            confidence=verdict.confidence,
            action_required=verdict.verdict not in ("NO_ACTION", "HOLD"),
            summary=getattr(verdict, "action", "") or "",
            trigger_source=event.trigger_source,
            verdict_event_id=echoed_verdict_event_id,
            reasoning_summary=getattr(verdict, "reasoning_summary", "") or "",
            risk_signals=_to_tuple(getattr(verdict, "risk_signals", None)),
            next_watch_items=_to_tuple(getattr(verdict, "next_watch_items", None)),
            sources=_to_tuple(getattr(verdict, "sources", None)),
        )
        await self._bus.publish(completed)

        logger.info(
            "intelligence_listener.completed_emitted",
            verdict=completed.verdict,
            confidence=completed.confidence,
            action_required=completed.action_required,
            verdict_event_id=completed.verdict_event_id,
            risk_signal_count=len(completed.risk_signals),
            next_watch_count=len(completed.next_watch_items),
        )
