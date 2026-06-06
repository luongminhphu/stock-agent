"""
TrendBatchScheduler — pre-compute trend predictions before morning briefing.

Owner: briefing segment (schedule trigger owned by bot/scheduler adapter).
Pattern: mirrors AgendaScheduler — stateless, instantiate once per cron run.
Suggested cron: 06:45 ICT daily (45 min before BriefingRequestedEvent at 07:30).

Pipeline:
    WatchlistService.get_symbols(user_id)
        → TrendEngine.run_for_symbols()         [market segment — pure technical]
        → ThesisActiveContextQuery (optional)    [thesis segment — investor context]
        → TrendReasoningAgent.analyze() x N     [ai segment — LLM verdict]
        → TrendPredictionStore.store()          [briefing segment — in-process cache]
        → optional: push STRONG alerts to Discord

Boundary:
    - Does NOT write to DB.
    - Does NOT import briefing.service or briefing.formatter.
    - Does NOT call BriefingService — that is a separate concern at 07:30.
    - TrendEngine is market segment; imported lazily to keep boundary explicit.
    - TrendReasoningAgent is ai segment; injected via constructor.
    - ThesisActiveContextQuery is thesis segment; injected via constructor (optional).
      When None, thesis_context falls back to "N/A" (backward-compatible).

Error handling:
    - Failure for one symbol is isolated (asyncio.gather with return_exceptions).
    - Failure for one user does not block others.
    - run_for_user() always writes to store — even partial results are useful.
    - Thesis context fetch failure is silent — falls back to "N/A" per symbol.
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
        thesis_query=None,
    ) -> None:
        self._engine = trend_engine
        self._agent = reasoning_agent
        self._store = prediction_store
        self._watchlist = watchlist_service
        self._notifier = bot_notifier
        self._thesis_query = thesis_query  # ThesisActiveContextQuery | None

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

        # Step 2: Build thesis context for this user (soft-fail — falls back to "N/A")
        thesis_context = await self._build_thesis_context(user_id)

        # Step 3: AI reasoning per bundle (concurrent, isolated)
        predictions = await self._run_reasoning_batch(
            bundles=bundles,
            session=session,
            user_id=user_id,
            thesis_context=thesis_context,
        )

        # Step 4: Write to store
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

        # Step 5: Push strong alerts
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

    async def _build_thesis_context(self, user_id: str) -> str:
        """Fetch active theses and format as compact context string for AI agent.

        Returns "N/A" when:
          - thesis_query was not injected (backward-compat)
          - query fails for any reason
          - no active theses found

        Format (one line per thesis, readable by TrendReasoningAgent prompt):
          VHM: LONG | target 55,000 | stop 42,000 | "Growth momentum thesis"
          DGC: LONG | target 120,000 | stop 95,000 | "Phosphate supercycle"
        """
        if self._thesis_query is None:
            return "N/A"
        try:
            theses = await self._thesis_query.get_active_with_components(user_id)
            if not theses:
                return "N/A"

            lines: list[str] = []
            for t in theses:
                ticker = t.get("ticker", "")
                direction = t.get("direction", "LONG")
                target = t.get("target_price")
                stop = t.get("stop_loss")
                title = t.get("title", "") or t.get("summary", "")

                parts = [f"{ticker}: {direction}"]
                if target:
                    parts.append(f"target {target:,.0f}")
                if stop:
                    parts.append(f"stop {stop:,.0f}")
                if title:
                    short_title = title[:60].strip()
                    parts.append(f'"{short_title}"')
                lines.append(" | ".join(parts))

            result = "\n".join(lines)
            logger.debug(
                "trend_batch_scheduler.thesis_context_built",
                user_id=user_id,
                thesis_count=len(theses),
            )
            return result
        except Exception as exc:
            logger.warning(
                "trend_batch_scheduler.thesis_context_failed",
                user_id=user_id,
                error=str(exc),
            )
            return "N/A"

    async def _run_reasoning_batch(
        self,
        bundles: list,
        session,
        user_id: str,
        thesis_context: str = "N/A",
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
                thesis_context=thesis_context,
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
