"""TodayLoopDigest — projection cho GET /api/v1/today-loop (Wave F3).

Owner: readmodel segment. Chuyển từ ``api/routes/today_loop._build_today_loop`` —
api chỉ còn parse query + gọi hàm này.

Nguồn (tuần tự — AsyncSession không cho concurrent ops):
    attention_items   — DashboardService.get_attention_needed (alerts, stop-loss, overdue review)
    top_signals       — DashboardService.get_recent_signals (7 ngày, sort strength)
    market_mood       — DashboardService.get_scan_latest (bias/green_pct từ WatchlistScan)
    brief_summary     — DashboardService.get_brief_latest(phase="morning")
    thesis_digest     — get_theses_list(active) gắn flag low_conviction | overdue_review

Rule cờ thesis_digest (readmodel projection rule, không phải thesis domain rule):
    score < 70                                          → low_conviction
    days_since_review > 14 OR health_rank ∈ {no_review, stale} → overdue_review
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.bootstrap import get_quote_service
from src.readmodel.dashboard_service import DashboardService

_OVERDUE_REVIEW_DAYS = 14  # mirror dashboard_service constant
_LOW_CONVICTION_THRESHOLD = 70  # score < 70 → flag low_conviction


async def _safe(coro: Any, label: str, stale_sources: list[str]) -> Any:
    """Await coro; on any exception append label to stale_sources and return None."""
    try:
        return await coro
    except Exception:
        stale_sources.append(label)
        return None


async def build_today_loop_digest(
    session: AsyncSession,
    user_id: str,
    *,
    enrich_prices: bool = True,
    attention_limit: int = 20,
    signal_limit: int = 10,
    quote_service: Any | None = None,
) -> dict[str, Any]:
    """Gom 5 nguồn readmodel thành 1 digest cho GET /api/v1/today-loop.

    Không gọi AI, không mutate. Nguồn lỗi → ghi vào ``stale_sources`` và trả partial
    (route luôn 200). ``quote_service`` None → lấy từ bootstrap khi enrich_prices.
    """
    stale_sources: list[str] = []
    svc = DashboardService(session)

    price_map: dict[str, float] = {}
    if enrich_prices:
        try:
            theses_for_price = await svc.get_theses_list(user_id, status="active", limit=500)
            tickers = list({t["ticker"] for t in theses_for_price if t.get("ticker")})
            if tickers:
                quote_svc = quote_service or get_quote_service()
                quotes = await quote_svc.get_bulk_quotes(tickers)
                price_map = {q.ticker: q.price for q in quotes if q.price}
        except Exception:
            stale_sources.append("price_map")

    attention_result = await _safe(
        svc.get_attention_needed(user_id, price_map=price_map, limit=attention_limit),
        label="attention",
        stale_sources=stale_sources,
    )
    attention_items: list[dict[str, Any]] = []
    if attention_result is not None:
        for item in attention_result.items:
            attention_items.append(item.model_dump() if hasattr(item, "model_dump") else dict(item))

    top_signals: list[dict[str, Any]] = (
        await _safe(
            svc.get_recent_signals(user_id, days=7, limit=signal_limit, stale_days=3),
            label="top_signals",
            stale_sources=stale_sources,
        )
        or []
    )

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
        narrative = brief_raw.get("summary") or brief_raw.get("content")
        brief_summary = {
            "narrative": narrative,
            "phase": brief_raw.get("phase", "morning"),
            "created_at": brief_raw.get("created_at"),
            "brief_id": brief_raw.get("id"),
            "feedback_outcome": brief_raw.get("feedback_outcome"),
        }

    thesis_digest: list[dict[str, Any]] = []
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
        "generated_at": datetime.now(UTC).isoformat(),
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
