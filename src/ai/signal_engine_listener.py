"""
SignalEngineListener — AI Segment, Intelligence Loop Wave 1

Subscribes to SignalEngineRequestedEvent on the global event bus.
Runs SignalEngineAgent to cross-check watchlist × thesis × portfolio.
Emits SignalEngineCompletedEvent so BriefingListener can inject the
resulting narrative into morning/eod brief context.

Owner: ai segment. Adapter only — no domain logic here.
Domain logic lives in ai/agents/signal_engine.py.

Wire-up: call SignalEngineListener(ai_client, ...).register() during
app bootstrap (e.g. in bot app.py on_ready, after all services are up).

Event flow:
    bot.SignalEngineScheduler
        → SignalEngineRequestedEvent
        → [this listener]
        → SignalEngineAgent.run()
        → SignalEngineCompletedEvent
        → briefing.BriefingListener (injects summary into brief context)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.ai.agents.signal_engine import SignalEngineAgent
from src.platform.event_bus import get_event_bus
from src.platform.events import SignalEngineCompletedEvent, SignalEngineRequestedEvent
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.ai.client import AIClient

logger = get_logger(__name__)


class SignalEngineListener:
    """
    Listens for SignalEngineRequestedEvent and runs SignalEngineAgent.

    Lifecycle::

        listener = SignalEngineListener(
            ai_client=ai_client,
            watchdog_service=watchdog_service,
            stress_test_service=stress_test_service,
            thesis_query=thesis_query,
            portfolio_query=portfolio_query,   # optional
            feedback_service=feedback_service, # optional
        )
        listener.register()  # call once in on_ready, after bootstrap

    Dependencies are injected to keep this adapter thin — no service
    instantiation inside the listener.
    """

    def __init__(
        self,
        ai_client: AIClient,
        watchdog_service: Any,
        stress_test_service: Any,
        thesis_query: Any,
        portfolio_query: Any | None = None,
        feedback_service: Any | None = None,
    ) -> None:
        self._agent = SignalEngineAgent(ai_client)
        self._watchdog = watchdog_service
        self._stress = stress_test_service
        self._thesis = thesis_query
        self._portfolio = portfolio_query
        self._feedback = feedback_service
        self._registered = False

    def register(self) -> None:
        """Subscribe handler to the global event bus.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._registered:
            logger.warning("SignalEngineListener already registered — skipping.")
            return
        get_event_bus().subscribe_handler(SignalEngineRequestedEvent, self._handle)
        self._registered = True
        logger.info("SignalEngineListener registered on event bus.")

    # ── internal ───────────────────────────────────────────────────────────

    async def _handle(self, event: SignalEngineRequestedEvent) -> None:
        """Handle SignalEngineRequestedEvent.

        1. Fetch inputs from watchdog, stress_test, thesis, portfolio.
        2. Run SignalEngineAgent.
        3. Emit SignalEngineCompletedEvent with counts + AI narrative summary.

        Failures are caught and logged — a failed engine run must never
        crash the event bus or block the briefing scheduler.
        """
        logger.info(
            "signal_engine_listener.started",
            phase=event.phase,
            triggered_by=event.triggered_by,
            user_id=event.user_id,
        )

        try:
            # -- 1. Gather inputs ------------------------------------------
            watchdog_outputs = await self._fetch_watchdog_outputs(event.user_id)
            stress_outputs = await self._fetch_stress_outputs(event.user_id)
            active_theses = await self._fetch_active_theses(event.user_id)
            portfolio_data = await self._fetch_portfolio(event.user_id)
            feedback_summary = await self._fetch_feedback_summary(event.user_id)

            # -- 2. Run agent -----------------------------------------------
            output = await self._agent.run(
                watchdog_outputs=watchdog_outputs,
                stress_outputs=stress_outputs,
                active_theses=active_theses,
                portfolio_data=portfolio_data,
                feedback_summary=feedback_summary,
            )

            # -- 3. Emit completed event ------------------------------------
            completed = SignalEngineCompletedEvent(
                phase=event.phase,
                ranked_signal_count=len(output.ranked_signals),
                thesis_review_trigger_count=len(output.thesis_review_triggers),
                risk_alert_count=len(output.risk_alerts),
                opportunity_count=len(output.opportunity_windows),
                summary=output.reasoning_summary or output.signal_summary,
            )
            await get_event_bus().publish(completed)

            logger.info(
                "signal_engine_listener.completed",
                phase=event.phase,
                signals=completed.ranked_signal_count,
                review_triggers=completed.thesis_review_trigger_count,
                risk_alerts=completed.risk_alert_count,
                opportunities=completed.opportunity_count,
                confidence=output.confidence,
                signal_summary=output.signal_summary,
            )

        except Exception as exc:
            logger.exception(
                "signal_engine_listener.failed",
                phase=event.phase,
                user_id=event.user_id,
                error=str(exc),
            )
            # Emit a zero-count completed event so BriefingListener isn't
            # blocked waiting for a signal engine summary that never arrives.
            fallback = SignalEngineCompletedEvent(
                phase=event.phase,
                ranked_signal_count=0,
                thesis_review_trigger_count=0,
                risk_alert_count=0,
                opportunity_count=0,
                summary="Signal engine unavailable — brief generated without cross-check.",
            )
            await get_event_bus().publish(fallback)

    # ── data fetchers — thin wrappers, each returns [] / {} on failure ──────

    async def _fetch_watchdog_outputs(self, user_id: str) -> list[dict[str, Any]]:
        """Fetch latest WatchdogOutput dicts for all active watchlist items."""
        try:
            results = await self._watchdog.get_latest_outputs(user_id=user_id)
            return [r.model_dump() if hasattr(r, "model_dump") else r for r in results]
        except Exception as exc:
            logger.warning("signal_engine_listener.watchdog_fetch_failed", error=str(exc))
            return []

    async def _fetch_stress_outputs(self, user_id: str) -> list[dict[str, Any]]:
        """Fetch latest StressTestOutput dicts for active theses."""
        try:
            results = await self._stress.get_latest_outputs(user_id=user_id)
            return [r.model_dump() if hasattr(r, "model_dump") else r for r in results]
        except Exception as exc:
            logger.warning("signal_engine_listener.stress_fetch_failed", error=str(exc))
            return []

    async def _fetch_active_theses(self, user_id: str) -> list[dict[str, Any]]:
        """Fetch active thesis summaries including assumptions + catalysts.

        Including assumptions/catalysts enables deep thesis cross-check
        (prompt rule 12 in signal_engine prompt). Falls back to [] on error.
        """
        try:
            return await self._thesis.get_active_with_components(user_id=user_id)
        except Exception as exc:
            logger.warning("signal_engine_listener.thesis_fetch_failed", error=str(exc))
            return []

    async def _fetch_portfolio(self, user_id: str) -> dict[str, Any] | None:
        """Fetch portfolio snapshot. Returns None if service not wired or fails."""
        if self._portfolio is None:
            return None
        try:
            return await self._portfolio.get_portfolio(user_id=user_id)
        except Exception as exc:
            logger.warning("signal_engine_listener.portfolio_fetch_failed", error=str(exc))
            return None

    async def _fetch_feedback_summary(self, user_id: str) -> str:
        """Fetch pre-rendered feedback calibration string.

        Returns empty string if service not wired or fails — agent degrades
        gracefully (skips feedback calibration, rule 13).
        """
        if self._feedback is None:
            return ""
        try:
            return await self._feedback.render_calibration_string(user_id=user_id)
        except Exception as exc:
            logger.warning("signal_engine_listener.feedback_fetch_failed", error=str(exc))
            return ""
