"""ProactiveAlertAgent — Wave 5 full implementation.

Owner: ai segment.
Boundary:
  - Subscribes to SignalDetectedEvent on the EventBus.
  - Calls AIClient → ProactiveAlertOutput → publishes RecommendationReadyEvent.
  - Marks signal_events.processed_at via WatchlistService (watchlist public API).
  - NEVER imports Discord, bot, or scheduler internals.
  - NEVER imports watchlist.models or watchlist.repository directly.
  - NEVER imports thesis.repository directly.

Bootstrap contract (enforced by bootstrap.py)::

    agent = get_proactive_alert_agent(ai_client=..., session_factory=...)
    agent.register()   # subscribes handler on bus; idempotent

Session strategy:
    agent receives a session_factory (async context manager factory) rather
    than a fixed session — each handler invocation opens its own short-lived
    session to avoid long-lived transactions across async gaps.
    session_factory is optional — mark_processed is silently skipped when None.

Context injection (Wave 2):
    _run_ai_analysis() fetches InvestorContext via ContextBuilder before
    building the user prompt. Uses settings.owner_user_id (single-user mode).
    Context fetch failures are swallowed — AI call always proceeds, degrading
    gracefully to a context-free prompt.

Thesis ID resolution (Wave 3):
    Within the same session block as context fetch, ThesisService is called to
    resolve the active thesis_id for the signal's symbol. Result is set on
    RecommendationReadyEvent.thesis_id so downstream consumers (bot embed,
    readmodel) can link the recommendation to the correct thesis without a
    secondary DB lookup. Falls back to "" when no active thesis found.

mark_processed boundary fix (Wave 4):
    _mark_processed() now calls WatchlistService.mark_signal_processed(event_id)
    instead of importing watchlist.models / watchlist.repository directly.
    ai segment only knows watchlist.service (public API).

Memory logging (Wave 6):
    _log_proactive_alert_interaction() fires after every successful AI analysis.
    Each real-time signal event is an episodic memory entry — the richest
    single-event data point for semantic synthesis downstream.
    Uses session_factory (same pattern as _mark_processed) — opens its own
    short-lived session so memory write never shares a transaction with
    mark_processed or context fetch.
    Never raises — all exceptions are caught and logged as warnings.
    Silently skips when session_factory is None or owner_user_id is not set.

Memory logging field mapping (Wave 6 fix):
    ai_verdict    ← output.urgency  (MONITORING / ALERT / CRITICAL)
                    This is the dashboard-facing verdict for alert episodes.
                    output.action (BUY/HOLD/SELL) is recorded in ai_key_points.
    ai_confidence ← output.confidence
    ai_risk_signals ← plain-text lines from output.risk_signals[].description
                      (never Python repr)
    ai_key_points ← human-readable summary: action + urgency + signal context
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

    Flow per event:
      1. Fetch InvestorContext + resolve thesis_id via ThesisService
      2. build_user_prompt() from event fields + investor context
      3. AIClient.chat() → ProactiveAlertOutput (structured JSON)
      4. Publish RecommendationReadyEvent (with thesis_id + rich Wave 7 fields)
      5. mark_processed() via WatchlistService (best-effort)
      6. log_interaction() → ai.memory episodic entry (best-effort)
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
          - memory log failure → log warning (non-blocking)
        """
        logger.info(
            "proactive_alert_agent.signal_received",
            symbol=event.symbol,
            signal_type=event.signal_type,
            strength=event.strength,
            confidence=event.confidence,
            event_id=event.event_id,
        )

        # ── Step 1+2+3: AI analysis with investor context ───────────────────
        output, resolved_thesis_id = await self._run_ai_analysis(event)
        if output is None:
            return  # error already logged inside _run_ai_analysis

        # ── Step 4: Publish RecommendationReadyEvent ────────────────────────
        rec_event = await self._publish_recommendation(event, output, resolved_thesis_id)

        # ── Step 5: Mark signal_event processed via WatchlistService (best-effort)
        await self._mark_processed(event.event_id)

        # ── Step 6: Log episodic memory entry (best-effort) ─────────────────
        await _log_proactive_alert_interaction(
            session_factory=self._session_factory,
            event=event,
            output=output,
        )

        if rec_event:
            logger.info(
                "proactive_alert_agent.done",
                symbol=event.symbol,
                action=output.action,
                urgency=output.urgency,
                confidence=output.confidence,
                recommendation_id=rec_event.recommendation_id,
                thesis_id=resolved_thesis_id,
            )

    async def _run_ai_analysis(
        self, event: SignalDetectedEvent
    ) -> tuple[ProactiveAlertOutput | None, str]:
        """Fetch investor context + resolve thesis_id, then call AIClient.

        Returns (output, thesis_id_str). output is None on AI failure.
        thesis_id_str is "" when no active thesis found or on any lookup error.
        Both context fetch and thesis_id lookup failures are swallowed so a
        DB hiccup never blocks the AI call.
        """
        investor_context_str = ""
        resolved_thesis_id = ""

        if self._session_factory:
            try:
                from src.platform.config import settings
                from src.ai.context_builder import ContextBuilder, render_for_agent
                from src.thesis.service import ThesisService

                async with self._session_factory() as session:
                    # Fetch investor context
                    ctx = await ContextBuilder(session).build(
                        user_id=settings.owner_user_id or None
                    )
                    investor_context_str = render_for_agent(ctx)

                    # Resolve active thesis_id for this symbol
                    thesis_svc = ThesisService(session)
                    thesis_id = await thesis_svc.get_active_thesis_id_for_ticker(
                        ticker=event.symbol,
                        user_id=settings.owner_user_id or None,
                    )
                    resolved_thesis_id = thesis_id or ""

            except Exception as ctx_exc:
                logger.warning(
                    "proactive_alert_agent.context_fetch_failed",
                    symbol=event.symbol,
                    error=str(ctx_exc),
                )
                # degrade gracefully — AI proceeds with empty context

        try:
            user_prompt = build_user_prompt(
                symbol=event.symbol,
                signal_type=event.signal_type,
                strength=event.strength,
                confidence=event.confidence,
                source=event.source,
                metadata=event.metadata,
                investor_context=investor_context_str,
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
                has_investor_context=bool(investor_context_str),
                resolved_thesis_id=resolved_thesis_id,
            )
            return output, resolved_thesis_id
        except Exception as exc:
            logger.error(
                "proactive_alert_agent.ai_call_failed",
                symbol=event.symbol,
                event_id=event.event_id,
                error=str(exc),
            )
            return None, ""

    async def _publish_recommendation(
        self,
        event: SignalDetectedEvent,
        output: ProactiveAlertOutput,
        resolved_thesis_id: str = "",
    ) -> RecommendationReadyEvent | None:
        """Build and publish RecommendationReadyEvent with rich fields.

        thesis_id is resolved upstream (Wave 3) and passed in directly.
        """
        try:
            bus = get_event_bus()
            rec_event = RecommendationReadyEvent(
                symbol=event.symbol,
                action=output.action,
                urgency=output.urgency,
                confidence=output.confidence,
                source_agent="proactive_alert",
                reasoning=getattr(output, "reasoning", "") or "",
                action_detail=getattr(output, "action_detail", "") or "",
                risk_signals=tuple(getattr(output, "risk_signals", []) or []),
                next_watch_items=tuple(getattr(output, "next_watch_items", []) or []),
                thesis_id=resolved_thesis_id,
            )
            await bus.publish(rec_event)
            logger.info(
                "proactive_alert_agent.recommendation_published",
                symbol=event.symbol,
                action=output.action,
                urgency=output.urgency,
                recommendation_id=rec_event.recommendation_id,
                thesis_id=resolved_thesis_id,
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

        Uses WatchlistService.mark_signal_processed() — ai segment never
        imports watchlist.models or watchlist.repository directly (Wave 4).
        Silently skips when session_factory is None (tests / no-DB mode).
        """
        if self._session_factory is None:
            logger.debug(
                "proactive_alert_agent.mark_processed_skipped",
                reason="no session_factory",
                event_id=event_id,
            )
            return

        try:
            from src.watchlist.service import WatchlistService

            async with self._session_factory() as session:
                svc = WatchlistService(session)
                found = await svc.mark_signal_processed(event_id)
                await session.commit()
                if found:
                    logger.debug(
                        "proactive_alert_agent.signal_event_marked_processed",
                        event_id=event_id,
                    )
                else:
                    logger.debug(
                        "proactive_alert_agent.signal_event_not_found",
                        event_id=event_id,
                    )
        except Exception as exc:
            logger.warning(
                "proactive_alert_agent.mark_processed_failed",
                event_id=event_id,
                error=str(exc),
            )


# ---------------------------------------------------------------------------
# Memory interaction logger — module-level helper (Wave 6)
# ---------------------------------------------------------------------------


async def _log_proactive_alert_interaction(
    session_factory: Any,
    event: SignalDetectedEvent,
    output: ProactiveAlertOutput,
) -> None:
    """Fire-and-forget episodic memory log for proactive alert interactions.

    Field mapping (canonical — matches AIInteractionLog schema comments):
      ai_verdict    ← output.urgency  (MONITORING / ALERT / CRITICAL)
                      Urgency is the dashboard-facing verdict for alert episodes.
                      output.action (BUY/HOLD/SELL) goes into ai_key_points.
      ai_confidence ← output.confidence
      ai_risk_signals ← plain-text lines extracted from output.risk_signals
                        Each RiskSignal's .description joined by newline.
                        Never Python repr — dashboard reads this as plain text.
      ai_key_points ← human-readable 1-liner: "action=HOLD signal=BREAKOUT ..."

    Never raises — all exceptions are caught and logged as warnings.
    Silently skips when session_factory is None or owner_user_id unset.
    """
    if session_factory is None:
        return
    try:
        from src.platform.config import settings
        user_id = settings.owner_user_id or None
        if not user_id:
            return

        from src.ai.memory.memory_service import InteractionEntry, MemoryService

        confidence_val = output.confidence if output.confidence is not None else 0.0
        risk_signals_raw = getattr(output, "risk_signals", []) or []

        # Extract plain-text descriptions — never store Python repr
        risk_lines: list[str] = []
        for rs in risk_signals_raw[:5]:
            if isinstance(rs, str):
                risk_lines.append(rs.strip())
            else:
                desc = getattr(rs, "description", None) or str(rs)
                if desc:
                    risk_lines.append(desc.strip())
        ai_risk_signals = "\n".join(risk_lines) if risk_lines else None

        # ai_verdict = urgency token (what the dashboard shows as the verdict label)
        urgency: str = (getattr(output, "urgency", None) or "MONITORING").upper()

        # ai_key_points = compact human-readable summary
        action: str = (getattr(output, "action", None) or "HOLD").upper()
        ai_key_points = (
            f"action={action} signal={event.signal_type} "
            f"strength={event.strength:.2f} conf={confidence_val:.0%}"
        )

        entry = InteractionEntry(
            user_id=user_id,
            agent_type="proactive_alert",
            trigger=f"signal:{event.signal_type}",
            tickers=[event.symbol] if event.symbol else [],
            ai_verdict=urgency,
            ai_confidence=confidence_val,
            ai_key_points=ai_key_points,
            ai_risk_signals=ai_risk_signals,
        )

        async with session_factory() as session:
            await MemoryService.log_interaction(session, entry)
            await session.commit()

    except Exception as exc:
        logger.warning("proactive_alert_agent.memory_log_failed", error=str(exc))


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
