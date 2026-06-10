"""RRG (Relative Rotation Graph) API route.

Owner: api segment — thin adapter only.
Endpoint: GET /api/v1/rrg/thesis

Flow:
    1. Query DB: ACTIVE + WEAKENING thesis tickers for owner
    2. Call RRGService.compute(tickers, benchmark, lookback_weeks, trail_points)
    3. Serialise + return

No business logic here — all computation lives in market.rrg_service.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_db, get_ohlcv_service
from src.market.ohlcv_service import OHLCVService
from src.market.rrg_service import RRGService
from src.readmodel.cache import DashboardTTLCache
from src.thesis.models import Thesis, ThesisStatus

router = APIRouter(prefix="/rrg", tags=["rrg"])

# Scope: ACTIVE + WEAKENING (thesis is still in play)
_ACTIVE_STATUSES = {ThesisStatus.ACTIVE, ThesisStatus.WEAKENING}

# Module-level cache shared across requests (same process).
# TTL = 10 min: RRG uses weekly OHLCV — intraday re-fetches are wasteful.
_cache = DashboardTTLCache()


@router.get("/thesis")
async def get_rrg_thesis(
    benchmark:      str = Query(default="VNINDEX", description="Benchmark ticker"),
    lookback_weeks: int = Query(default=26,        ge=4,  le=52),
    trail_points:   int = Query(default=8,         ge=3,  le=20),
    session: AsyncSession = Depends(get_db),
    user_id: str          = Depends(get_current_user_id),
    ohlcv_svc: OHLCVService = Depends(get_ohlcv_service),
) -> dict[str, Any]:
    """Return RRG coordinates for all active thesis tickers.

    Each ticker entry contains:
      - ticker, quadrant (leading|weakening|lagging|improving)
      - rs_ratio, rs_momentum (current position)
      - trail: list of {rs_ratio, rs_momentum} — oldest → newest, weekly sampled
      - error: null on success, string on data failure
    """
    # Cache key encodes all query params that affect the result.
    cache_extra = f"{benchmark}:{lookback_weeks}:{trail_points}"
    cached = _cache.get("rrg", user_id, extra=cache_extra)
    if cached is not None:
        return cached

    # 1. Fetch active thesis tickers from DB
    stmt = (
        select(Thesis.ticker)
        .where(
            Thesis.user_id == user_id,
            Thesis.status.in_([s.value for s in _ACTIVE_STATUSES]),
        )
        .distinct()
    )
    rows = (await session.execute(stmt)).all()
    tickers = [row[0] for row in rows]

    if not tickers:
        return {
            "benchmark":      benchmark,
            "as_of":          None,
            "lookback_weeks": lookback_weeks,
            "trail_points":   trail_points,
            "tickers":        [],
        }

    # 2. Compute RRG
    svc    = RRGService(ohlcv_service=ohlcv_svc)
    result = await svc.compute(
        tickers=tickers,
        benchmark=benchmark,
        lookback_weeks=lookback_weeks,
        trail_points=trail_points,
    )

    # 3. Serialise — convert dataclasses → JSON-friendly dicts
    def _serialise_ticker(t: Any) -> dict[str, Any]:
        return {
            "ticker":      t.ticker,
            "quadrant":    t.quadrant,
            "rs_ratio":    t.rs_ratio,
            "rs_momentum": t.rs_momentum,
            "trail":       [{"rs_ratio": p.rs_ratio, "rs_momentum": p.rs_momentum}
                            for p in t.trail],
            "error":       t.error,
        }

    response = {
        "benchmark":      result.benchmark,
        "as_of":          result.as_of,
        "lookback_weeks": result.lookback_weeks,
        "trail_points":   result.trail_points,
        "tickers":        [_serialise_ticker(t) for t in result.tickers],
    }
    _cache.set("rrg", user_id, response, extra=cache_extra)
    return response
