"""
TrendBatchScheduler — pre-compute trend predictions before morning briefing.

Owner: briefing segment (schedule trigger owned by bot/scheduler adapter).
Pattern: mirrors AgendaScheduler — stateless, instantiate once per cron run.
Suggested cron: 06:45 ICT daily (45 min before BriefingRequestedEvent at 07:30).

Pipeline:
    WatchlistService.get_symbols(user_id)
        → TrendEngine.run_for_symbols()         [market segment — pure technical]
        → TrendReasoningAgent.analyze() x N     [ai segment — LLM verdict]
        → TrendPredictionStore.store()          [briefing segment — in-process cache]
        → optional: push STRONG alerts to Discord

Boundary:
    - Does NOT write to DB.
    - Does NOT import briefing.service or briefing.formatter.
    - Does NOT call BriefingService — that is a separate concern at 07:30.
    - TrendEngine is market segment; imported lazily to keep boundary explicit.
    - TrendReasoningAgent is ai segment; injected via constructor.

Error handling:
    - Failure for one symbol is isolated (asyncio.gather with return_exceptions).
    - Failure for one user does not block others.
    - run_for_user() always writes to store — even partial results are useful.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.ai.agents.trend_reasoning import TrendPrediction, TrendReasoningAgent
    from src.briefing.trend_prediction_store import TrendPredictionStore
    from src.market.trend_engine import TrendEngine

logger = get_logger(__name__)

# Verdicts that warrant a proactive Discord push (not just briefing inclusion)
_ALERT_VERDICTS = frozenset({"STRONG_BUY", "STRONG_SELL"})
_MIN_ALERT_CONFIDENCE = 0.68


class TrendBatchScheduler:
    """Orchestrate batch trend pre-computation for one or many users.

    Args:
        trend_engine:        TrendEngine instance (market segment).
        reasoning_agent:     TrendReasoningAgent instance (ai segment).
        prediction_store:    TrendPredictionStore instance (briefing segment).
        watchlist_service:   WatchlistService instance (watchlist segment).
        bot_notifier:        Optional — push strong alerts to Discord before briefing.
    """

    def __init__(
        self,
        trend_engine: "TrendEngine",
        reasoning_agent: "TrendReasoningAgent",
        prediction_store: "TrendPredictionStore",
        watchlist_service,
        bot_notifier=None,
    ) -> None:
        self._engine = trend_engine
        self._agent = reasoning_agent
        self._store = prediction_store
        self._watchlist = watchlist_service
        self._notifier = bot_notifier

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_for_user(
        self,
        user_id: str,
        session=None,
    ) -> list["TrendPrediction"]:
        """Pre-compute trend predictions for all watchlist symbols of one user.

        Steps:
        1. Fetch watchlist symbols.
        2. Batch-compute TechnicalSignalBundle for all symbols (asyncio.gather).
        3. Run TrendReasoningAgent per symbol (asyncio.gather, isolated errors).
        4. Write results to TrendPredictionStore.
        5. Push STRONG_BUY / STRONG_SELL alerts via notifier if configured.

        Returns list of TrendPrediction (may include fallback entries).
        """
        symbols = await self._get_symbols(user_id, session)
        if not symbols:
            logger.warning("trend_batch_scheduler.no_symbols", user_id=user_id)
            return []

        logger.info(
            "trend_batch_scheduler.start",
            user_id=user_id,
            symbol_count=len(symbols),
            symbols=symbols,
        )

        # Step 1: Batch technical signals
        bundles = await self._engine.run_for_symbols(symbols)

        # Step 2: AI reasoning per bundle (concurrent, isolated)
        predictions = await self._run_reasoning_batch(
            bundles=bundles,
            session=session,
            user_id=user_id,
        )

        # Step 3: Write to store
        self._store.store(predictions)

        logger.info(
            "trend_batch_scheduler.complete",
            user_id=user_id,
            total=len(predictions),
            actionable=sum(1 for p in predictions if p.is_actionable),
            strong_signals=[
                p.symbol for p in predictions if p.verdict in _ALERT_VERDICTS
            ],
        )

        # Step 4: Push strong alerts
        await self._push_strong_alerts(predictions, user_id)

        return predictions

    async def run_all(
        self,
        user_ids: list[str],
        session=None,
    ) -> None:
        """Batch run for multiple users. Error per user is isolated."""
        for uid in user_ids:
            try:
                await self.run_for_user(uid, session=session)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "trend_batch_scheduler.user_failed",
                    user_id=uid,
                    error=str(exc),
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_symbols(self, user_id: str, session) -> list[str]:
        """Fetch watchlist symbols. Returns [] on failure."""
        try:
            items = await self._watchlist.get_watchlist(user_id=user_id, session=session)
            return [item.symbol.upper() for item in (items or [])]
        except Exception as exc:
            logger.warning(
                "trend_batch_scheduler.watchlist_failed",
                user_id=user_id,
                error=str(exc),
            )
            return []

    async def _run_reasoning_batch(
        self,
        bundles: list,
        session,
        user_id: str,
    ) -> list["TrendPrediction"]:
        """Run TrendReasoningAgent for each bundle concurrently.

        Uses asyncio.gather with return_exceptions=True so one slow/failing
        symbol does not block the rest. Failed symbols get a technical fallback
        derived from composite score.
        """
        from src.ai.agents.trend_reasoning import _fallback_prediction

        async def _analyze_one(bundle) -> "TrendPrediction":
            return await self._agent.analyze(
                bundle=bundle,
                thesis_context="N/A",  # Wave 3: inject ThesisQueryService
                session=session,
                user_id=user_id,
            )

        results = await asyncio.gather(
            *[_analyze_one(b) for b in bundles],
            return_exceptions=True,
        )

        predictions: list["TrendPrediction"] = []
        for bundle, result in zip(bundles, results):
            if isinstance(result, Exception):
                logger.warning(
                    "trend_batch_scheduler.reasoning_failed",
                    symbol=bundle.symbol,
                    error=str(result),
                )
                predictions.append(_fallback_prediction(bundle.symbol, bundle.composite))
            else:
                predictions.append(result)

        return predictions

    async def _push_strong_alerts(
        self,
        predictions: list["TrendPrediction"],
        user_id: str,
    ) -> None:
        """Push STRONG_BUY / STRONG_SELL signals via notifier if configured."""
        if self._notifier is None:
            return

        strong = [
            p for p in predictions
            if p.verdict in _ALERT_VERDICTS
            and p.confidence >= _MIN_ALERT_CONFIDENCE
        ]

        if not strong:
            return

        logger.info(
            "trend_batch_scheduler.pushing_alerts",
            user_id=user_id,
            count=len(strong),
            symbols=[p.symbol for p in strong],
        )

        try:
            await self._notifier.push_trend_alerts(user_id, strong)
        except Exception as exc:
            logger.warning(
                "trend_batch_scheduler.alert_push_failed",
                user_id=user_id,
                error=str(exc),
            )
