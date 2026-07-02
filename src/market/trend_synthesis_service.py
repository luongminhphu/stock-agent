"""TrendSynthesisService — orchestrate RRG + TrendEngine + AI for one ticker.

Owner: market segment (data orchestration).
Caller: api/routes/trend.py  GET /api/v1/trend/ticker/{ticker}

Flow:
    1. Fetch OHLCV candles (90 days) → TrendEngine.compute() → raw_indicators
    2. Fetch RRG position from RRGService (1-ticker compute, 26-week lookback)
    3. Detect trail pattern from RRG trail
    4. Pass composite payload to TrendSynthesisAgent (ai segment)
    5. Return TrendSynthesisResponse (DTO for API)

Boundary:
    - This service owns the orchestration only — no domain logic.
    - TrendEngine and RRGService are injected for testability.
    - AI call is delegated to TrendSynthesisAgent (ai segment).
    - Does not read/write DB directly.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.market.rrg_service import RRGService
from src.market.trend_engine import TrendEngine
from src.platform.logging import get_logger

logger = get_logger(__name__)

# OHLCV lookback for indicator computation (90 trading days ≈ 4.5 months)
_OHLCV_DAYS = 90
# RRG parameters — match the thesis RRG defaults
_RRG_LOOKBACK_WEEKS = 26
_RRG_TRAIL_POINTS = 13  # bi-weekly samples over 26 weeks


def _detect_trail_pattern(trail: list[Any]) -> str:
    """Rule-based trail pattern detection from RRGPoint list.

    Returns one of: ENTERING_LEADING | EXITING_LEADING | ENTERING_IMPROVING |
    DEEP_LAGGING | WEAKENING_FAST | RECOVERY | ROTATING | STABLE
    """
    if not trail or len(trail) < 3:
        return "STABLE"

    # Use last 4 points for pattern detection
    recent = trail[-4:]
    prev = trail[-5] if len(trail) >= 5 else trail[0]

    curr = recent[-1]
    prev3 = recent[-3] if len(recent) >= 3 else recent[0]

    curr_leading = curr.rs_ratio > 100 and curr.rs_momentum > 100
    curr_improving = curr.rs_ratio < 100 and curr.rs_momentum > 100
    curr_lagging = curr.rs_ratio < 100 and curr.rs_momentum < 100
    curr_weakening = curr.rs_ratio > 100 and curr.rs_momentum < 100

    prev_lagging = prev.rs_ratio < 100 and prev.rs_momentum < 100
    prev_improving = prev.rs_ratio < 100 and prev.rs_momentum > 100
    prev_leading = prev.rs_ratio > 100 and prev.rs_momentum > 100

    # Cross detections
    if curr_leading and prev_improving:
        return "ENTERING_LEADING"
    if curr_weakening and prev_leading:
        return "EXITING_LEADING"
    if curr_improving and prev_lagging:
        return "ENTERING_IMPROVING"

    # Velocity checks
    if curr_weakening:
        mom_delta = curr.rs_momentum - prev3.rs_momentum
        if mom_delta < -3.0:
            return "WEAKENING_FAST"

    if curr_lagging:
        # Check if recovering (momentum tăng trong lagging)
        if curr.rs_momentum > prev3.rs_momentum + 1.5:
            return "RECOVERY"
        if curr.rs_ratio < 95 and curr.rs_momentum < 97:
            return "DEEP_LAGGING"

    # General rotation
    ratio_delta = curr.rs_ratio - prev3.rs_ratio
    mom_delta = curr.rs_momentum - prev3.rs_momentum
    if abs(ratio_delta) > 2.0 or abs(mom_delta) > 2.0:
        return "ROTATING"

    return "STABLE"


class TrendSynthesisService:
    """Orchestrate RRG + TrendEngine + AI synthesis for a single ticker."""

    def __init__(
        self,
        rrg_service: RRGService,
        trend_engine: TrendEngine,
        synthesis_agent: Any,  # TrendSynthesisAgent — lazy import to avoid circular
    ) -> None:
        self._rrg = rrg_service
        self._engine = trend_engine
        self._agent = synthesis_agent

    async def run(self, ticker: str) -> dict[str, Any]:
        """Run full trend analysis for one ticker.

        Returns a dict suitable for direct JSON serialisation by the API route.
        Never raises — returns error dict on failure.
        """
        ticker = ticker.upper()
        try:
            return await self._run(ticker)
        except Exception as exc:
            logger.warning(
                "trend_synthesis_service.run_failed",
                ticker=ticker,
                error=str(exc),
            )
            return {"ticker": ticker, "error": str(exc)}

    async def _run(self, ticker: str) -> dict[str, Any]:
        import asyncio

        # 1. Compute TrendEngine bundle + RRG in parallel.
        # return_exceptions=True: partial 429 → degrade gracefully instead of crash.
        engine_task = asyncio.create_task(
            self._engine.run_for_symbol(ticker)
        )
        rrg_task = asyncio.create_task(
            self._rrg.compute(
                tickers=[ticker],
                benchmark="VNINDEX",
                lookback_weeks=_RRG_LOOKBACK_WEEKS,
                trail_points=_RRG_TRAIL_POINTS,
            )
        )
        results = await asyncio.gather(engine_task, rrg_task, return_exceptions=True)
        bundle_obj, rrg_response = results[0], results[1]

        # If both fail (e.g. full 429 outage) → raise so run() returns error dict
        bundle_failed = isinstance(bundle_obj, BaseException)
        rrg_failed    = isinstance(rrg_response, BaseException)
        if bundle_failed and rrg_failed:
            raise bundle_obj  # type: ignore[misc]

        if bundle_failed:
            logger.warning(
                "trend_synthesis.engine_failed",
                ticker=ticker,
                error=str(bundle_obj),
            )
        if rrg_failed:
            logger.warning(
                "trend_synthesis.rrg_failed",
                ticker=ticker,
                error=str(rrg_response),
            )

        # 2. Extract indicator values — TechnicalSignalBundle is a Pydantic model
        if not bundle_failed:
            bundle_dict = bundle_obj.model_dump() if hasattr(bundle_obj, "model_dump") else dict(bundle_obj)  # type: ignore[union-attr]
        else:
            bundle_dict = {}
        raw_indicators = bundle_dict.get("raw_indicators") or {}
        regime = bundle_dict.get("regime", "UNKNOWN")
        composite = float(bundle_dict.get("composite", 0.5))

        # 3. Extract RRG position (graceful when RRG fetch failed)
        rrg_ticker = None
        if not rrg_failed:
            rrg_ticker = next(
                (t for t in rrg_response.tickers if t.ticker == ticker and not t.error),  # type: ignore[union-attr]
                None,
            )
        rrg_data: dict[str, Any] = {}
        if rrg_ticker:
            trail_pattern = _detect_trail_pattern(rrg_ticker.trail)
            rrg_data = {
                "quadrant": rrg_ticker.quadrant,
                "rs_ratio": rrg_ticker.rs_ratio,
                "rs_momentum": rrg_ticker.rs_momentum,
                "trail_pattern": trail_pattern,
                "trail": [asdict(p) for p in rrg_ticker.trail],
            }
        else:
            rrg_data = {
                "quadrant": "unknown",
                "rs_ratio": 100.0,
                "rs_momentum": 100.0,
                "trail_pattern": "STABLE",
                "trail": [],
            }

        # 4. AI synthesis
        synthesis_payload = {
            "ticker": ticker,
            "rrg": rrg_data,
            "raw_indicators": raw_indicators,
            "regime": regime,
            "composite": composite,
        }
        synthesis = await self._agent.run(synthesis_payload)

        # 5. Build response
        return {
            "ticker": ticker,
            "rrg": rrg_data,
            "indicators": {
                "rsi": raw_indicators.get("rsi", 50),
                "macd": {
                    "line": raw_indicators.get("macd_line", 0),
                    "signal": raw_indicators.get("macd_signal", 0),
                    "histogram": raw_indicators.get("macd_hist", 0),
                    "cross": raw_indicators.get("macd_cross", "bearish_cross"),
                },
                "cmf": raw_indicators.get("cmf", 0),
                "adx": {
                    "value": raw_indicators.get("adx", 0),
                    "plus_di": raw_indicators.get("adx_plus_di", 0),
                    "minus_di": raw_indicators.get("adx_minus_di", 0),
                },
                "regime": regime,
                "composite": composite,
            },
            "synthesis": synthesis.model_dump(),
        }
