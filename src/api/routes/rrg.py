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

from src.ai.agents.rrg_rotation import RRGRotationAgent
from src.ai.schemas.rrg_rotation import RRGRotationSignal
from src.api.deps import get_ai_client, get_current_user_id, get_db, get_ohlcv_service, get_symbol_registry
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
    trail_points:   int = Query(default=0,         ge=0,  le=52),
    extra:          str = Query(default="",        description="Extra tickers (comma-separated) appended to thesis list"),
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
    # Auto trail_points: if caller passes 0 (default), derive from lookback.
    # 26W → 8 pts (quarterly granularity), 52W → 16 pts (bi-weekly granularity).
    # Formula: 1 trail point per ~(lookback_weeks / 8) weeks, clamped 8-26.
    if trail_points == 0:
        trail_points = max(8, min(26, lookback_weeks // 2))

    # Parse + sanitise extra tickers (uppercase, max 10, alphanumeric only)
    extra_tickers: list[str] = []
    if extra:
        for t in extra.split(","):
            sym = t.strip().upper()
            if sym and sym.isalnum() and len(sym) <= 10:
                extra_tickers.append(sym)
        extra_tickers = extra_tickers[:10]

    cache_extra = f"{benchmark}:{lookback_weeks}:{trail_points}:{','.join(sorted(extra_tickers))}"
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
    thesis_tickers = [row[0] for row in rows]

    # Merge extra tickers — deduplicate, preserve thesis order first
    seen = set(thesis_tickers)
    tickers = thesis_tickers + [t for t in extra_tickers if t not in seen]

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
        "extra_tickers":  extra_tickers,   # FE uses this to style chips differently
    }
    _cache.set("rrg", user_id, response, extra=cache_extra)
    return response


# ── RRG AI Rotation Signal ────────────────────────────────────────────────────

# Cache rotation signals separately — TTL 5 min (AI call is expensive).
_rotation_cache = DashboardTTLCache()
_ROTATION_TTL   = 300  # 5 min


@router.get("/rotation/{ticker}")
async def get_rrg_rotation(
    ticker:         str,
    lookback_weeks: int = Query(default=26, ge=4, le=52),
    user_id: str              = Depends(get_current_user_id),
    ohlcv_svc: OHLCVService   = Depends(get_ohlcv_service),
    ai_client: object         = Depends(get_ai_client),
    registry: object          = Depends(get_symbol_registry),
) -> dict[str, Any]:
    """Return AI rotation signal for a single ticker.

    Fetches RRG data for the ticker (same pipeline as /rrg/thesis) then
    calls RRGRotationAgent to produce pattern + signal + opportunity.

    Cached 5 min per (user, ticker, lookback_weeks).
    Falls back to rule-based signal on AI failure — never 500.
    """
    ticker_upper = ticker.upper()
    cache_extra  = f"{ticker_upper}:{lookback_weeks}"
    cached = _rotation_cache.get("rrg_rotation", user_id, extra=cache_extra)
    if cached is not None:
        return cached

    # 1. Compute RRG for this single ticker (reuse RRGService)
    trail_points = max(8, min(26, lookback_weeks // 2))
    svc    = RRGService(ohlcv_service=ohlcv_svc)
    result = await svc.compute(
        tickers=[ticker_upper],
        benchmark="VNINDEX",
        lookback_weeks=lookback_weeks,
        trail_points=trail_points,
    )

    if not result.tickers:
        return {"error": f"Không lấy được dữ liệu RRG cho {ticker_upper}"}

    t = result.tickers[0]
    if t.error:
        return {"error": t.error}

    # 2. Resolve sector + company name from registry
    sector       = ""
    company_name = ""
    try:
        info = registry.get(ticker_upper)  # type: ignore[attr-defined]
        if info:
            sector       = str(info.sector.value) if info.sector else ""
            company_name = info.name or ""
    except Exception:
        pass  # registry miss is non-fatal

    # 3. Call AI agent
    trail_dicts = [{"rs_ratio": p.rs_ratio, "rs_momentum": p.rs_momentum} for p in t.trail]
    agent  = RRGRotationAgent(ai_client=ai_client)  # type: ignore[arg-type]
    signal: RRGRotationSignal = await agent.analyze(
        ticker=ticker_upper,
        quadrant=t.quadrant,
        rs_ratio=t.rs_ratio,
        rs_momentum=t.rs_momentum,
        trail=trail_dicts,
        sector=sector,
        company_name=company_name,
        lookback_weeks=lookback_weeks,
    )

    response: dict[str, Any] = {
        "ticker":        signal.ticker,
        "quadrant":      signal.quadrant,
        "pattern":       signal.pattern,
        "signal":        signal.signal,
        "signal_reason": signal.signal_reason,
        "opportunity":   signal.opportunity,
        "risk":          signal.risk,
        "next_watch":    signal.next_watch,
        "confidence":    signal.confidence,
        "rs_ratio":      t.rs_ratio,
        "rs_momentum":   t.rs_momentum,
        "sector":        sector,
        "company_name":  company_name,
    }
    _rotation_cache.set("rrg_rotation", user_id, response, ttl=_ROTATION_TTL, extra=cache_extra)
    return response
