"""ProactiveAlertAgent — Wave 5 full implementation.

Owner: ai segment.
Boundary:
  - Subscribes to SignalDetectedEvent on the EventBus.
  - Calls AIClient → ProactiveAlertOutput → publishes RecommendationReadyEvent.
  - Marks signal_events.processed_at via SignalEventRepository (best-effort).
  - NEVER imports Discord, bot, or scheduler internals.
  - NEVER writes to signal_events directly (read + mark_processed only).

Bootstrap contract (enforced by bootstrap.py)::

    agent = get_proactive_alert_agent(ai_client=..., session_factory=...)
    agent.register()   # subscribes handler on bus; idempotent

Session strategy:
    agent receives a session_factory (async context manager factory) rather
    than a fixed session — each handler invocation opens its own short-lived
    session to avoid long-lived transactions across async gaps.
    session_factory is optional — mark_processed is silently skipped when None.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.platform.event_bus import get_event_bus
from src.platform.events import RecommendationReadyEvent, SignalDetectedEvent
from src.platform.logging import get_logger
from src.ai.prompts.proactive_alert import (
    ProactiveAlertOutput,
    SYSTEM_PROMPT,
    build_user_prompt,
)

if TYPE_CHECKING:
    from src.ai.client import AIClient

logger = get_logger(__name__)

_instance: "ProactiveAlertAgent | None" = None


class ProactiveAlertAgent:
    """
    Listens for SignalDetectedEvent, calls AIClient for analysis,
    publishes RecommendationReadyEvent, and drains signal_events inbox.

    Wave 5 flow per event:
      1. build_user_prompt() from event fields
      2. AIClient.chat() → ProactiveAlertOutput (structured JSON)
      3. Publish RecommendationReadyEvent onto EventBus (with rich Wave 7 fields)
      4. mark_processed() in signal_events table (best-effort)
    """

    def __init__(
        self,
        ai_client: "AIClient",
        session_factory: Any = None,
    ) -> None:
        self._ai_client = ai_client
        self._session_factory = session_factory  # AsyncSessionFactory | None
        self._registered = False

    def register(self) -> None:
        """Subscribe to SignalDetectedEvent on global bus. Idempotent."""
        if self._registered:
            return
        bus = get_event_bus()
        bus.subscribe_handler(SignalDetectedEvent, self._handle_signal)
        self._registered = True
        logger.info("proactive_alert_agent.registered")

    async def _handle_signal(self, event: SignalDetectedEvent) -> None:
        """
        Core handler — called by EventBus worker for each SignalDetectedEvent.

        Failures are isolated per step:
          - AI failure  → log error + return (no partial publish)
          - Bus publish failure  → log error (mark_processed still attempted)
          - mark_processed failure → log warning (event stays in pending inbox)
        """
        logger.info(
            "proactive_alert_agent.signal_received",
            symbol=event.symbol,
            signal_type=event.signal_type,
            strength=event.strength,
            confidence=event.confidence,
            event_id=event.event_id,
        )

        # ── Step 1+2: AI analysis ───────────────────────────────────────────
        output = await self._run_ai_analysis(event)
        if output is None:
            return  # error already logged inside _run_ai_analysis

        # ── Step 3: Publish RecommendationReadyEvent ───────────────────────
        rec_event = await self._publish_recommendation(event, output)

        # ── Step 4: Mark signal_event processed (best-effort) ────────────
        await self._mark_processed(event.event_id)

        if rec_event:
            logger.info(
                "proactive_alert_agent.done",
                symbol=event.symbol,
                action=output.action,
                urgency=output.urgency,
                confidence=output.confidence,
                recommendation_id=rec_event.recommendation_id,
            )

    async def _run_ai_analysis(
        self, event: SignalDetectedEvent
    ) -> ProactiveAlertOutput | None:
        """Call AIClient and return parsed output. Returns None on any failure."""
        try:
            user_prompt = build_user_prompt(
                symbol=event.symbol,
                signal_type=event.signal_type,
                strength=event.strength,
                confidence=event.confidence,
                source=event.source,
                metadata=event.metadata,
            )
            output: ProactiveAlertOutput = await self._ai_client.chat(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=ProactiveAlertOutput,
                temperature=0.2,
                max_tokens=1024,
            )
            logger.info(
                "proactive_alert_agent.analysis_complete",
                symbol=event.symbol,
                action=output.action,
                urgency=output.urgency,
                confidence=output.confidence,
                risk_signals=len(output.risk_signals),
            )
            return output
        except Exception as exc:
            logger.error(
                "proactive_alert_agent.ai_call_failed",
                symbol=event.symbol,
                event_id=event.event_id,
                error=str(exc),
            )
            return None

    async def _publish_recommendation(
        self,
        event: SignalDetectedEvent,
        output: ProactiveAlertOutput,
    ) -> RecommendationReadyEvent | None:
        """Build and publish RecommendationReadyEvent with Wave 7 rich fields.

        Populates reasoning, action_detail, risk_signals, next_watch_items from
        ProactiveAlertOutput so build_recommendation_embed() can render a rich
        Discord embed without a secondary DB lookup.
        """
        try:
            bus = get_event_bus()
            rec_event = RecommendationReadyEvent(
                symbol=event.symbol,
                action=output.action,
                urgency=output.urgency,
                confidence=output.confidence,
                source_agent="proactive_alert",
                # Wave 7 rich fields — fall back to empty string/tuple when absent
                reasoning=getattr(output, "reasoning", "") or "",
                action_detail=getattr(output, "action_detail", "") or "",
                risk_signals=tuple(getattr(output, "risk_signals", []) or []),
                next_watch_items=tuple(getattr(output, "next_watch_items", []) or []),
                thesis_id=str(getattr(output, "thesis_id", "") or ""),
            )
            await bus.publish(rec_event)
            logger.info(
                "proactive_alert_agent.recommendation_published",
                symbol=event.symbol,
                action=output.action,
                urgency=output.urgency,
                recommendation_id=rec_event.recommendation_id,
            )
            return rec_event
        except Exception as exc:
            logger.error(
                "proactive_alert_agent.publish_failed",
                symbol=event.symbol,
                event_id=event.event_id,
                error=str(exc),
            )
            return None

    async def _mark_processed(self, event_id: str) -> None:
        """Best-effort: mark signal_events row processed_at = now(UTC).

        Silently skips when session_factory is None (tests / no-DB mode).
        Lookup is by event_id (UUID) which is unique per row.
        """
        if self._session_factory is None:
            logger.debug(
                "proactive_alert_agent.mark_processed_skipped",
                reason="no session_factory",
                event_id=event_id,
            )
            return

        try:
            from sqlalchemy import select
            from src.watchlist.models import SignalEvent
            from src.watchlist.repository import SignalEventRepository

            async with self._session_factory() as session:
                stmt = select(SignalEvent).where(SignalEvent.event_id == event_id)
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row is None:
                    logger.debug(
                        "proactive_alert_agent.signal_event_not_found",
                        event_id=event_id,
                    )
                    return
                repo = SignalEventRepository(session)
                await repo.mark_processed(row)
                await session.commit()
                logger.debug(
                    "proactive_alert_agent.signal_event_marked_processed",
                    event_id=event_id,
                )
        except Exception as exc:
            logger.warning(
                "proactive_alert_agent.mark_processed_failed",
                event_id=event_id,
                error=str(exc),
            )


def get_proactive_alert_agent(
    ai_client: "AIClient",
    session_factory: Any = None,
) -> ProactiveAlertAgent:
    """Return singleton ProactiveAlertAgent. Creates on first call.

    Args:
        ai_client:       AIClient singleton from bootstrap.
        session_factory: Async session factory (e.g. async_session from db.py).
                         Optional — mark_processed is skipped when None.
    """
    global _instance
    if _instance is None:
        _instance = ProactiveAlertAgent(
            ai_client=ai_client,
            session_factory=session_factory,
        )
    return _instance


def reset_proactive_alert_agent() -> None:
    """Reset singleton — for tests only."""
    global _instance
    _instance = None
