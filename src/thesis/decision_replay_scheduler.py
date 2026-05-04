"""Scheduler: auto-evaluate expired decision horizons and persist AI lessons.

Owner: thesis segment (schedule trigger owned by bot/scheduler adapter).

This module is the bridge that closes the learning loop:

  DecisionLog (outcome_evaluated_at=None, horizon reached)
      |-- evaluate_outcome()       → fill PnL + verdict
      |-- analyze_decision()       → ReplayAgent returns key_lesson + pattern
      |-- persist_lesson()         → write lesson/pattern back to DecisionLog

The bot/scheduler calls run_pending() on a cron (e.g. daily at 08:00).
No domain logic lives here — all rules stay in DecisionService + ReplayAgent.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.thesis.decision_service import DecisionReplayEnvelope, DecisionService

logger = get_logger(__name__)


class DecisionReplayScheduler:
    """Orchestrates the nightly replay evaluation batch.

    Designed to be stateless — instantiate once per cron run, then discard.
    Inject DecisionService (with session, quote_service, replay_agent attached).
    """

    def __init__(self, decision_service: "DecisionService") -> None:
        self._svc = decision_service

    async def run_pending(self) -> list["DecisionReplayEnvelope"]:
        """Evaluate all decisions that reached their horizon.

        Steps per decision:
        1. evaluate_outcome()  — fetch current price, compute PnL, write verdict.
        2. analyze_decision()  — call ReplayAgent for lesson + pattern.
        3. persist_lesson()    — write key_lesson + pattern_detected back to DB.

        Errors per decision are caught and logged; other decisions continue.

        Returns:
            List of DecisionReplayEnvelope for successfully processed decisions.
        """
        pending = await self._svc.list_pending_outcome_evaluations()

        if not pending:
            logger.info("replay_scheduler.no_pending")
            return []

        logger.info("replay_scheduler.start", pending_count=len(pending))
        results: list[DecisionReplayEnvelope] = []

        for row in pending:
            try:
                envelope = await self._process_one(row.id)
                results.append(envelope)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "replay_scheduler.decision_failed",
                    decision_id=row.id,
                    ticker=row.ticker,
                    error=str(exc),
                    exc_info=True,
                )

        logger.info(
            "replay_scheduler.done",
            processed=len(results),
            failed=len(pending) - len(results),
        )
        return results

    async def _process_one(self, decision_id: int) -> "DecisionReplayEnvelope":
        """Full pipeline for a single decision."""
        # Step 1: outcome evaluation (PnL + verdict)
        await self._svc.evaluate_outcome(decision_id)
        logger.debug("replay_scheduler.outcome_evaluated", decision_id=decision_id)

        # Step 2: AI replay analysis
        envelope = await self._svc.analyze_decision(decision_id)
        logger.debug(
            "replay_scheduler.analysis_done",
            decision_id=decision_id,
            verdict=envelope.outcome_verdict,
        )

        # Step 3: persist lesson only if ReplayAgent returned something
        if envelope.replay is not None:
            key_lesson = getattr(envelope.replay, "key_lesson", None)
            pattern_detected = getattr(envelope.replay, "pattern_detected", None)
            await self._svc.persist_lesson(
                decision_id,
                key_lesson=key_lesson,
                pattern_detected=pattern_detected,
            )

        return envelope
