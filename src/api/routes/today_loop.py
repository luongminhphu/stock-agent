"""GET /api/v1/today-loop — Daily investor intelligence summary.

Owner: api segment — thin adapter only.
Delegates 100% to DashboardService (readmodel segment).
No AI calls. No mutation. Pure read + aggregate.

Endpoints:
    GET /api/v1/today-loop            — single-user (owner_user_id from .env)
    GET /api/v1/today-loop/{user_id}  — multi-user

Response shape:
    attention_items   — urgent tasks (alerts, stop-loss, overdue review, catalyst)
    top_signals       — top-N ranked signals (by strength) from recent signal events
    brief_summary     — latest morning brief narrative + metadata
    thesis_digest     — active theses flagged: low_conviction | overdue_review
    market_mood       — scan-level bias from latest WatchlistScan JSON
    generated_at      — ISO UTC timestamp of this aggregation
    stale_sources     — list of sources that failed (partial results ok, no 500)
    meta              — quick-count summary for UI badges

Design notes:
- All 5 sources run sequentially (AsyncSession does not allow concurrent ops).
- get_attention_needed() and get_scan_latest() open their own isolated sessions
  internally (see DashboardService) — no ISCE risk from this route.
- thesis_digest flags are derived from get_theses_list() fields:
    score < 70          → low_conviction
    days_since_review > 14 OR health_rank in (no_review, stale) → overdue_review
- stale_sources is non-empty when a source raises; the route still returns 200
  with partial data so the UI can degrade gracefully.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db
from src.platform.bootstrap import get_quote_service
from src.platform.config import settings
from src.readmodel.dashboard_service import DashboardService

router = APIRouter(prefix="/today-loop", tags=["today-loop"])

_OVERDUE_REVIEW_DAYS = 14  # mirror dashboard_service constant
_LOW_CONVICTION_THRESHOLD = 70  # score < 70 → flag low_conviction


def _default_user_id() -> str:
    if not settings.owner_user_id:
        raise HTTPException(
            status_code=500,
            detail="owner_user_id is not configured. Set it in .env for single-user mode.",
        )
    return settings.owner_user_id


async def _safe(coro, label: str, stale_sources: list[str]) -> Any:
    """Await coro; on any exception append label to stale_sources and return None."""
    try:
        return await coro
    except Exception:
        stale_sources.append(label)
        return None


async def _build_today_loop(
    session: AsyncSession,
    user_id: str,
    enrich_prices: bool,
    attention_limit: int,
    signal_limit: int,
) -> dict[str, Any]:
    stale_sources: list[str] = []
    svc = DashboardService(session)

    price_map: dict[str, float] = {}
    if enrich_prices:
        try:
            theses_for_price = await svc.get_theses_list(
                user_id, status="active", limit=500
            )
            tickers = list({t["ticker"] for t in theses_for_price if t.get("ticker")})
            if tickers:
                quote_svc = get_quote_service()
                quotes = await quote_svc.get_bulk_quotes(tickers)
                price_map = {q.ticker: q.price for q in quotes if q.price}
        except Exception:
            stale_sources.append("price_map")

    attention_result = await _safe(
        svc.get_attention_needed(user_id, price_map=price_map, limit=attention_limit),
        label="attention",
        stale_sources=stale_sources,
    )
    attention_items: list[dict] = []
    if attention_result is not None:
        for item in attention_result.items:
            attention_items.append(
                item.model_dump() if hasattr(item, "model_dump") else dict(item)
            )

    top_signals: list[dict] = await _safe(
        svc.get_recent_signals(user_id, days=7, limit=signal_limit, stale_days=3),
        label="top_signals",
        stale_sources=stale_sources,
    ) or []

    scan_snapshot = await _safe(
        svc.get_scan_latest(user_id),
        label="scan_snapshot",
        stale_sources=stale_sources,
    )
    market_mood: dict[str, Any] = {}
    if scan_snapshot:
        market_mood = {
            "bias": scan_snapshot.get("market_bias") or scan_snapshot.get("bias"),
            "green_pct": scan_snapshot.get("green_pct"),
            "scanned_at": scan_snapshot.get("scanned_at"),
            "summary_raw": scan_snapshot.get("summary"),
        }

    brief_raw = await _safe(
        svc.get_brief_latest(user_id, phase="morning"),
        label="brief",
        stale_sources=stale_sources,
    )
    brief_summary: dict[str, Any] = {}
    if brief_raw:
        narrative = (
            brief_raw.get("summary")
            or brief_raw.get("content")
        )
        brief_summary = {
            "narrative": narrative,
            "phase": brief_raw.get("phase", "morning"),
            "created_at": brief_raw.get("created_at"),
            "brief_id": brief_raw.get("id"),
            "feedback_outcome": brief_raw.get("feedback_outcome"),
        }

    thesis_digest: list[dict] = []
    try:
        all_active = await svc.get_theses_list(
            user_id,
            status="active",
            limit=100,
            price_map=price_map,
        )
        for t in all_active:
            flags: list[str] = []

            score = t.get("score")
            if score is not None and score < _LOW_CONVICTION_THRESHOLD:
                flags.append("low_conviction")

            health = t.get("health_rank")
            days_since = t.get("days_since_review")
            if health in ("no_review", "stale") or (
                days_since is not None and days_since > _OVERDUE_REVIEW_DAYS
            ):
                flags.append("overdue_review")

            if flags:
                thesis_digest.append(
                    {
                        "thesis_id": t.get("id"),
                        "ticker": t.get("ticker"),
                        "score": score,
                        "health_rank": health,
                        "days_since_review": days_since,
                        "last_verdict": t.get("last_verdict"),
                        "pnl_pct": t.get("pnl_pct"),
                        "flags": flags,
                    }
                )
    except Exception:
        stale_sources.append("thesis_digest")

    return {
        "user_id": user_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "attention_items": attention_items,
        "top_signals": top_signals,
        "brief_summary": brief_summary,
        "thesis_digest": thesis_digest,
        "market_mood": market_mood,
        "stale_sources": stale_sources,
        "meta": {
            "attention_count": len(attention_items),
            "signal_count": len(top_signals),
            "thesis_needing_action": len(thesis_digest),
            "has_brief": bool(brief_summary),
            "has_market_mood": bool(market_mood.get("bias")),
        },
    }


@router.get("/{user_id}")
async def get_today_loop(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    enrich_prices: Annotated[
        bool,
        Query(
            description="Fetch live prices from QuoteService for stop_loss proximity check and thesis P&L."
        ),
    ] = True,
    attention_limit: Annotated[
        int,
        Query(ge=1, le=50, description="Max attention items returned."),
    ] = 20,
    signal_limit: Annotated[
        int,
        Query(ge=1, le=50, description="Max signal items returned (sorted by strength desc)."),
    ] = 10,
) -> dict[str, Any]:
    """Daily investor intelligence loop cho một user cụ thể.

    Aggregates 5 sources in one call:
      1. attention_items  — urgent tasks (alerts, stop-loss, overdue review, upcoming catalyst)
      2. top_signals      — ranked signals from recent 7-day signal events
      3. brief_summary    — latest morning brief narrative
      4. thesis_digest    — active theses flagged for action
      5. market_mood      — scan-level market bias from latest WatchlistScan

    Always returns 200. stale_sources lists any source that failed.
    No AI calls. No mutation. Pure read + aggregate.
    """
    return await _build_today_loop(
        session=session,
        user_id=user_id,
        enrich_prices=enrich_prices,
        attention_limit=attention_limit,
        signal_limit=signal_limit,
    )


@router.get("")
async def get_today_loop_single_user(
    session: Annotated[AsyncSession, Depends(get_db)],
    enrich_prices: Annotated[
        bool,
        Query(
            description="Fetch live prices from QuoteService for stop_loss proximity check and thesis P&L."
        ),
    ] = True,
    attention_limit: Annotated[
        int,
        Query(ge=1, le=50, description="Max attention items returned."),
    ] = 20,
    signal_limit: Annotated[
        int,
        Query(ge=1, le=50, description="Max signal items returned (sorted by strength desc)."),
    ] = 10,
) -> dict[str, Any]:
    """Single-user alias — dùng owner_user_id từ .env."""
    return await _build_today_loop(
        session=session,
        user_id=_default_user_id(),
        enrich_prices=enrich_prices,
        attention_limit=attention_limit,
        signal_limit=signal_limit,
    )
