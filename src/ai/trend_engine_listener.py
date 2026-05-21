"""
TrendEngineListener — AI Segment, Wave 2 (Trend Prediction)

Co-subscribes SignalEngineRequestedEvent alongside SignalEngineListener.
Runs TrendEngine (technical signals) + TrendReasoningAgent (AI verdict)
per symbol in the user's watchlist. Persists results to TrendPredictionStore.
Emits TrendPredictionCompletedEvent so BriefingListener / bot can consume
top verdicts without re-running the engine.

Owner: ai segment. Thin adapter — no domain logic here.
Domain logic: market.TrendEngine (signals), ai.TrendReasoningAgent (verdict).

Wire-up: call TrendEngineListener(...).register() in platform.bootstrap
after all singletons are initialised.

Event flow:
    bot.SignalEngineScheduler
        → SignalEngineRequestedEvent
        → [SignalEngineListener]         (existing, untouched)
        → [this listener]                (NEW — co-subscribes same event)
        → WatchlistQueryService.get_latest_outputs()
        → TrendEngine.run_for_symbols()
        → TrendReasoningAgent.analyze() × N  (asyncio.gather, independent)
        → TrendPredictionStore.save()
        → TrendPredictionCompletedEvent
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from src.platform.event_bus import get_event_bus
from src.platform.events import (
    SignalEngineRequestedEvent,
    TrendPredictionCompletedEvent,
)
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.ai.agents.trend_reasoning import TrendReasoningAgent
    from src.market.trend_engine import TrendEngine
    from src.readmodel.trend_prediction_store import TrendPredictionStore

logger = get_logger(__name__)


class TrendEngineListener:
    """
    Listens for SignalEngineRequestedEvent and runs the trend prediction pipeline.

    Lifecycle::

        listener = TrendEngineListener(
            trend_reasoning_agent=trend_reasoning_agent,
            trend_engine=trend_engine,
            prediction_store=trend_prediction_store,
            watchlist_query=WatchlistQueryService(session_factory=AsyncSessionLocal),
            thesis_query=ThesisQueryService(session_factory=AsyncSessionLocal),
        )
        listener.register()  # call once in bootstrap, after bus.start()

    Dependencies are injected — this adapter contains no service instantiation.
    """

    def __init__(
        self,
        trend_reasoning_agent: TrendReasoningAgent,
        trend_engine: TrendEngine,
        prediction_store: TrendPredictionStore,
        watchlist_query: Any,
        thesis_query: Any | None = None,
    ) -> None:
        self._agent = trend_reasoning_agent
        self._engine = trend_engine
        self._store = prediction_store
        self._watchlist_query = watchlist_query
        self._thesis_query = thesis_query
        self._registered = False

    def register(self) -> None:
        """Subscribe handler to the global event bus.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._registered:
            logger.warning("TrendEngineListener already registered — skipping.")
            return
        get_event_bus().subscribe_handler(SignalEngineRequestedEvent, self._handle)
        self._registered = True
        logger.info("TrendEngineListener registered on event bus.")

    # ── internal ───────────────────────────────────────────────────────────

    async def _handle(self, event: SignalEngineRequestedEvent) -> None:
        """Handle SignalEngineRequestedEvent.

        1. Fetch watchlist tickers from WatchlistQueryService.
        2. Run TrendEngine batch to compute TechnicalSignalBundles.
        3. Run TrendReasoningAgent per bundle (independent, failures isolated).
        4. Persist each TrendPrediction to TrendPredictionStore.
        5. Emit TrendPredictionCompletedEvent with top verdicts.

        Failures are caught and logged — never crashes the event bus.
        """
        logger.info(
            "trend_engine_listener.started",
            phase=event.phase,
            triggered_by=event.triggered_by,
            user_id=event.user_id,
        )

        try:
            # 1. Tickers from watchlist (via thesis health snapshots)
            tickers = await self._fetch_watchlist_tickers(event.user_id)
            if not tickers:
                logger.warning(
                    "trend_engine_listener.no_tickers",
                    user_id=event.user_id,
                )
                await self._emit_completed(event.phase, [])
                return

            # 2. Technical signal bundles (batch)
            bundles = await self._engine.run_for_symbols(tickers)

            # 3. AI reasoning per bundle — independent, gather with exceptions
            tasks = [self._analyze_one(bundle) for bundle in bundles]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 4. Persist successes, log failures
            predictions = []
            for ticker, result in zip(tickers, results):
                if isinstance(result, Exception):
                    logger.warning(
                        "trend_engine_listener.symbol_failed",
                        ticker=ticker,
                        error=str(result),
                    )
                else:
                    self._store.save(ticker, result)
                    predictions.append(result)

            # 5. Emit completed
            await self._emit_completed(event.phase, predictions)

            logger.info(
                "trend_engine_listener.completed",
                phase=event.phase,
                analyzed=len(predictions),
                failed=len(tickers) - len(predictions),
            )

        except Exception as exc:
            logger.exception(
                "trend_engine_listener.failed",
                phase=event.phase,
                user_id=event.user_id,
                error=str(exc),
            )
            await self._emit_completed(event.phase, [])

    async def _analyze_one(self, bundle: Any) -> Any:
        """Run TrendReasoningAgent for a single TechnicalSignalBundle."""
        thesis_context = await self._fetch_thesis_context(
            getattr(bundle, "symbol", "")
        )
        return await self._agent.analyze(
            bundle,
            thesis_context=thesis_context,
        )

    async def _emit_completed(
        self, phase: str, predictions: list[Any]
    ) -> None:
        top = sorted(
            predictions,
            key=lambda p: getattr(p, "confidence", 0.0),
            reverse=True,
        )[:3]
        event = TrendPredictionCompletedEvent(
            scan_phase=phase,
            symbols_analyzed=len(predictions),
            top_verdicts=tuple(
                (getattr(p, "symbol", ""), getattr(p, "verdict", ""))
                for p in top
            ),
        )
        await get_event_bus().publish(event)

    # ── data fetchers — each returns safe fallback on failure ──────────────

    async def _fetch_watchlist_tickers(self, user_id: str) -> list[str]:
        """Fetch active watchlist tickers via WatchlistQueryService.

        WatchlistQueryService.get_latest_outputs() returns thesis health
        snapshots, each containing a 'ticker' field. Extracting tickers
        here avoids a direct WatchlistService (session-per-call) import.
        """
        try:
            outputs = await self._watchlist_query.get_latest_outputs(
                user_id=user_id
            )
            return [
                o["ticker"]
                for o in outputs
                if o.get("ticker")
            ]
        except Exception as exc:
            logger.warning(
                "trend_engine_listener.watchlist_fetch_failed",
                error=str(exc),
            )
            return []

    async def _fetch_thesis_context(self, symbol: str) -> str:
        """Fetch latest thesis summary string for symbol.

        Returns 'N/A' if thesis_query not wired or lookup fails.
        """
        if self._thesis_query is None:
            return "N/A"
        try:
            result = await self._thesis_query.get_latest_by_symbol(symbol)
            if result is None:
                return "N/A"
            return getattr(result, "summary", "N/A") or "N/A"
        except Exception as exc:
            logger.warning(
                "trend_engine_listener.thesis_fetch_failed",
                symbol=symbol,
                error=str(exc),
            )
            return "N/A"
