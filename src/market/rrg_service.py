"""RRG (Relative Rotation Graph) computation service.

Owner: market segment.
Callers: api/routes/rrg.py  (GET /api/v1/rrg/thesis)

Algorithm (JdK RS-Ratio / RS-Momentum):
    1. Fetch daily close for each ticker + benchmark over `lookback_weeks` weeks
    2. RS_raw[t]      = close_ticker[t] / close_benchmark[t]
    3. RS_Ratio[t]    = EMA(RS_raw, short) / EMA(RS_raw, long) × 100
    4. RS_Momentum[t] = EMA(RS_Ratio, short) / EMA(RS_Ratio, long) × 100
    5. Sample every ~5 trading days → weekly trail points
    6. Return last `trail_points` weekly samples per ticker

Quadrant classification (centre = 100):
    rs_ratio > 100, rs_momentum > 100  → leading
    rs_ratio > 100, rs_momentum < 100  → weakening
    rs_ratio < 100, rs_momentum < 100  → lagging
    rs_ratio < 100, rs_momentum > 100  → improving

Design notes:
- Pure computation — no DB calls, no AI calls.
- OHLCVService is injected for testability.
- VNINDEX benchmark fetch uses same VCIOHLCVAdapter (ticker "VNINDEX").
- EMA parameters: short=10, long=40 (standard JdK settings).
- If a ticker has insufficient data it is omitted from results (logged).
- asyncio.gather for parallel OHLCV fetches (one request per ticker).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Sequence

from src.market.ohlcv_service import Interval, OHLCVService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BENCHMARK_TICKER = "VNINDEX"
_EMA_SHORT        = 10
_EMA_LONG         = 40
# Minimum candles needed for EMA_LONG to produce a meaningful value.
# With _EMA_LONG=40 we need at least 40 candles before the first stable EMA.
_MIN_CANDLES      = _EMA_LONG + _EMA_SHORT + 5   # 55
# One weekly sample every ~5 trading days
_TRADING_DAYS_PER_WEEK = 5

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RRGPoint:
    """A single (RS-Ratio, RS-Momentum) point in the trail."""
    rs_ratio:    float
    rs_momentum: float


@dataclass
class RRGTicker:
    """RRG result for one ticker."""
    ticker:     str
    quadrant:   str           # leading | weakening | lagging | improving
    rs_ratio:   float         # current (latest) RS-Ratio
    rs_momentum: float        # current (latest) RS-Momentum
    trail:      list[RRGPoint] = field(default_factory=list)
    # oldest → newest; last element == current position
    error:      str | None    = field(default=None, compare=False)


@dataclass
class RRGResponse:
    """Full RRG response."""
    benchmark:  str
    as_of:      str                # ISO date
    lookback_weeks: int
    trail_points:   int
    tickers:    list[RRGTicker]


# ---------------------------------------------------------------------------
# EMA helper
# ---------------------------------------------------------------------------

def _ema(values: list[float], span: int) -> list[float]:
    """Exponential Moving Average — same formula as pandas ewm(span, adjust=False).

    Returns a list of the same length.  Values before index `span-1` are
    bootstrapped with a simple mean of the first `span` elements.
    """
    if not values:
        return []
    k   = 2.0 / (span + 1)
    out = [0.0] * len(values)
    # Seed: plain mean of first `min(span, len)` elements
    seed_n   = min(span, len(values))
    out[0]   = sum(values[:seed_n]) / seed_n
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


# ---------------------------------------------------------------------------
# Quadrant classifier
# ---------------------------------------------------------------------------

def _quadrant(rs_ratio: float, rs_momentum: float) -> str:
    above_r = rs_ratio   >= 100.0
    above_m = rs_momentum >= 100.0
    if above_r and above_m:
        return "leading"
    if above_r and not above_m:
        return "weakening"
    if not above_r and not above_m:
        return "lagging"
    return "improving"


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def _compute_rrg(
    ticker_closes:    list[float],
    benchmark_closes: list[float],
    trail_points:     int,
) -> tuple[list[RRGPoint], float, float] | None:
    """Compute RS-Ratio, RS-Momentum trail for one ticker vs benchmark.

    Returns (trail, current_rs_ratio, current_rs_momentum) or None on error.
    Lists must be same length and pre-aligned by date.
    """
    n = min(len(ticker_closes), len(benchmark_closes))
    if n < _MIN_CANDLES:
        return None

    tc = ticker_closes[-n:]
    bc = benchmark_closes[-n:]

    # Step 1 — RS raw
    rs_raw = [t / b for t, b in zip(tc, bc) if b != 0.0]
    if len(rs_raw) < _MIN_CANDLES:
        return None

    # Step 2 — RS-Ratio
    ema_s  = _ema(rs_raw, _EMA_SHORT)
    ema_l  = _ema(rs_raw, _EMA_LONG)
    rs_ratio_series = [
        (s / l * 100.0) if l != 0.0 else 100.0
        for s, l in zip(ema_s, ema_l)
    ]

    # Step 3 — RS-Momentum
    ema_rs_s = _ema(rs_ratio_series, _EMA_SHORT)
    ema_rs_l = _ema(rs_ratio_series, _EMA_LONG)
    rs_momentum_series = [
        (s / l * 100.0) if l != 0.0 else 100.0
        for s, l in zip(ema_rs_s, ema_rs_l)
    ]

    # Step 4 — Sample weekly trail (last `trail_points` weekly samples)
    # Each "week" = _TRADING_DAYS_PER_WEEK candles; sample the last candle of each week.
    # We take from the end of the series backwards.
    sampled: list[RRGPoint] = []
    idx = len(rs_ratio_series) - 1
    for _ in range(trail_points):
        if idx < 0:
            break
        sampled.append(RRGPoint(
            rs_ratio    = round(rs_ratio_series[idx], 3),
            rs_momentum = round(rs_momentum_series[idx], 3),
        ))
        idx -= _TRADING_DAYS_PER_WEEK
    sampled.reverse()   # oldest → newest

    current_r = sampled[-1].rs_ratio    if sampled else 100.0
    current_m = sampled[-1].rs_momentum if sampled else 100.0
    return sampled, current_r, current_m


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class RRGService:
    """Compute RRG coordinates for a list of thesis tickers.

    Args:
        ohlcv_service: Injected OHLCVService with VCIOHLCVAdapter wired.
    """

    def __init__(self, ohlcv_service: OHLCVService) -> None:
        self._ohlcv = ohlcv_service

    async def compute(
        self,
        tickers:       Sequence[str],
        benchmark:     str          = _BENCHMARK_TICKER,
        lookback_weeks: int         = 26,
        trail_points:  int          = 8,
    ) -> RRGResponse:
        """Fetch OHLCV + compute RRG for each ticker in parallel.

        Args:
            tickers:        List of thesis tickers (e.g. ["VHM", "DGC"]).
            benchmark:      Benchmark ticker — default "VNINDEX".
            lookback_weeks: How many weeks of history to fetch.
            trail_points:   How many weekly trail points to return.

        Returns:
            RRGResponse with per-ticker rs_ratio, rs_momentum, trail, quadrant.
        """
        today     = date.today()
        from_date = today - timedelta(weeks=lookback_weeks + 4)  # +4w buffer for gaps/holidays

        tickers_upper = [t.upper() for t in tickers]
        bench_upper   = benchmark.upper()
        all_tickers   = list(dict.fromkeys([bench_upper] + tickers_upper))  # dedup, bench first

        # ── Parallel OHLCV fetch ──────────────────────────────────────────
        async def _fetch(ticker: str) -> tuple[str, list[float]]:
            try:
                candles = await self._ohlcv.get_candles(
                    ticker, from_date=from_date, to_date=today, interval=Interval.D1
                )
                closes = [c.close for c in sorted(candles, key=lambda c: c.date)]
                return ticker, closes
            except Exception as exc:
                logger.warning("rrg: fetch failed for %s: %s", ticker, exc)
                return ticker, []

        results   = await asyncio.gather(*[_fetch(t) for t in all_tickers])
        closes_map: dict[str, list[float]] = dict(results)

        bench_closes = closes_map.get(bench_upper, [])
        if not bench_closes:
            logger.error("rrg: benchmark %s returned no data — abort", bench_upper)
            return RRGResponse(
                benchmark=bench_upper,
                as_of=today.isoformat(),
                lookback_weeks=lookback_weeks,
                trail_points=trail_points,
                tickers=[],
            )

        # ── Compute per ticker ────────────────────────────────────────────
        rrg_tickers: list[RRGTicker] = []
        for ticker in tickers_upper:
            tc = closes_map.get(ticker, [])
            if not tc:
                rrg_tickers.append(RRGTicker(
                    ticker=ticker, quadrant="lagging",
                    rs_ratio=100.0, rs_momentum=100.0,
                    error=f"no OHLCV data for {ticker}",
                ))
                continue

            # Align: use only dates covered by both series (take tail of longer)
            n   = min(len(tc), len(bench_closes))
            res = _compute_rrg(tc[-n:], bench_closes[-n:], trail_points)

            if res is None:
                rrg_tickers.append(RRGTicker(
                    ticker=ticker, quadrant="lagging",
                    rs_ratio=100.0, rs_momentum=100.0,
                    error=f"insufficient data ({len(tc)} candles, need {_MIN_CANDLES})",
                ))
                logger.warning("rrg: %s insufficient data (%d candles)", ticker, len(tc))
                continue

            trail, cur_r, cur_m = res
            rrg_tickers.append(RRGTicker(
                ticker=ticker,
                quadrant=_quadrant(cur_r, cur_m),
                rs_ratio=round(cur_r, 3),
                rs_momentum=round(cur_m, 3),
                trail=trail,
            ))
            logger.debug(
                "rrg: %s → %s  R=%.2f  M=%.2f  trail=%d pts",
                ticker, _quadrant(cur_r, cur_m), cur_r, cur_m, len(trail),
            )

        return RRGResponse(
            benchmark=bench_upper,
            as_of=today.isoformat(),
            lookback_weeks=lookback_weeks,
            trail_points=trail_points,
            tickers=rrg_tickers,
        )
