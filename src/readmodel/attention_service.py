"""AttentionService — aggregate attention items từ 4 nguồn.

Owner: readmodel segment.

Extracted from DashboardService to keep DashboardService as a thin facade.
All logic formerly in DashboardService.get_attention_needed() lives here.

Sources:
  1. triggered_alerts      — alerts đã fire, chưa dismiss
  2. overdue_reviews       — thesis active, không có AI review > 14 ngày
  3. upcoming_catalysts    — catalyst PENDING trong 72h tới
  4. stop_loss_proximity   — giá hiện tại cách stop_loss <= 3%

Cached 30s per (user_id, limit).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.db import AsyncSessionLocal
from src.platform.logging import get_logger
from src.readmodel.cache import DashboardTTLCache
from src.readmodel.schemas import AttentionItem, AttentionPanelResponse, AttentionUrgency

logger = get_logger(__name__)

# Module-level cache — shared across all AttentionService instances.
_cache = DashboardTTLCache()

# ---------------------------------------------------------------------------
# Cross-segment model imports (guarded)
# ---------------------------------------------------------------------------

try:
    from src.watchlist.models import Alert, AlertStatus

    _WATCHLIST_MODELS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _WATCHLIST_MODELS_AVAILABLE = False
    Alert = AlertStatus = None  # type: ignore[assignment,misc]

try:
    from src.thesis.models import Catalyst, CatalystStatus, Thesis, ThesisReview, ThesisStatus

    _THESIS_MODELS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _THESIS_MODELS_AVAILABLE = False
    Catalyst = CatalystStatus = Thesis = ThesisReview = ThesisStatus = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OVERDUE_REVIEW_DAYS: int = 14
_UPCOMING_CATALYST_HOURS: int = 72
_STOP_LOSS_PROXIMITY_PCT: float = 3.0
_ATTENTION_CACHE_TTL_SECS: int = 30

_URGENCY_ORDER = {
    AttentionUrgency.CRITICAL: 0,
    AttentionUrgency.HIGH: 1,
    AttentionUrgency.MEDIUM: 2,
}


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class AttentionService:
    """Aggregate attention panel for a user across 4 data sources.

    Stateless — no constructor args needed beyond session (unused,
    service opens its own dedicated session for all 4 sources).
    """

    def __init__(self, session: AsyncSession | None = None) -> None:
        # session param kept for DI consistency; not used internally
        # (service always opens a fresh AsyncSessionLocal context)
        pass

    async def get_attention_needed(
        self,
        user_id: str,
        price_map: dict[str, float] | None = None,
        limit: int = 20,
    ) -> AttentionPanelResponse:
        """Aggregate attention items sorted critical → high → medium.

        price_map: injected from route layer (QuoteService) — service does
                   not fetch prices itself.
        Partial results returned if one source fails — never raises.
        Cached 30s per (user_id, limit).
        """
        cache_extra = str(limit)
        cached = _cache.get("attention", user_id, extra=cache_extra)
        if cached is not None:
            return cached  # type: ignore[return-value]

        now = datetime.now(UTC)
        items: list[AttentionItem] = []
        seen: set[tuple[str, str, int | None]] = set()

        def _add(item: AttentionItem) -> None:
            key = (item.kind, item.ticker, item.thesis_id)
            if key not in seen:
                seen.add(key)
                items.append(item)

        async with AsyncSessionLocal() as session:

            # ---- Source 1: Triggered alerts --------------------------------
            if _WATCHLIST_MODELS_AVAILABLE:
                try:
                    alert_rows = (
                        await session.execute(
                            select(Alert)
                            .where(
                                Alert.user_id == user_id,
                                Alert.status == AlertStatus.TRIGGERED,
                            )
                            .order_by(Alert.triggered_at.desc())
                            .limit(50)
                        )
                    ).scalars().all()

                    for r in alert_rows:
                        priority = getattr(r, "priority", "medium") or "medium"
                        urgency = (
                            AttentionUrgency.CRITICAL
                            if priority == "critical"
                            else AttentionUrgency.HIGH
                            if priority == "high"
                            else AttentionUrgency.MEDIUM
                        )
                        triggered_at = r.triggered_at or now
                        if triggered_at.tzinfo is None:
                            triggered_at = triggered_at.replace(tzinfo=UTC)
                        label = getattr(r, "label", None) or str(getattr(r, "condition_type", "alert"))
                        _add(AttentionItem(
                            kind="triggered_alert",
                            ticker=r.ticker,
                            thesis_id=getattr(r, "thesis_id", None),
                            message=f"Alert: {label} @ {r.ticker}",
                            urgency=urgency,
                            ts=triggered_at,
                            metadata={
                                "alert_id": r.id,
                                "condition_type": str(getattr(r, "condition_type", "")),
                                "triggered_price": getattr(r, "triggered_price", None),
                                "threshold": getattr(r, "threshold", None),
                            },
                        ))
                except Exception as exc:
                    logger.warning("attention.source1_alerts.error", error=str(exc), exc_info=True)

            # ---- Source 2: Overdue reviews ---------------------------------
            if _THESIS_MODELS_AVAILABLE:
                try:
                    overdue_cutoff = now - timedelta(days=_OVERDUE_REVIEW_DAYS)

                    last_review_sq = (
                        select(
                            ThesisReview.thesis_id,
                            func.max(ThesisReview.reviewed_at).label("last_reviewed_at"),
                        )
                        .group_by(ThesisReview.thesis_id)
                        .subquery()
                    )

                    stmt = (
                        select(
                            Thesis.id,
                            Thesis.ticker,
                            Thesis.title,
                            Thesis.created_at,
                            last_review_sq.c.last_reviewed_at,
                        )
                        .outerjoin(last_review_sq, last_review_sq.c.thesis_id == Thesis.id)
                        .where(
                            Thesis.user_id == user_id,
                            Thesis.status == ThesisStatus.ACTIVE,
                        )
                        .where(
                            (last_review_sq.c.last_reviewed_at == None)  # noqa: E711
                            | (last_review_sq.c.last_reviewed_at < overdue_cutoff)
                        )
                        .order_by(last_review_sq.c.last_reviewed_at.asc().nulls_first())
                        .limit(20)
                    )

                    for row in (await session.execute(stmt)).all():
                        last_reviewed = row.last_reviewed_at
                        if last_reviewed is not None:
                            last_reviewed = _ensure_utc(last_reviewed)
                        created_at_utc = _ensure_utc(row.created_at)

                        if last_reviewed is None:
                            days_overdue = (now - created_at_utc).days
                            msg = f"{row.ticker}: chưa từng được review AI"
                        else:
                            days_overdue = (now - last_reviewed).days
                            msg = f"{row.ticker}: review AI cách đây {days_overdue} ngày"

                        _add(AttentionItem(
                            kind="overdue_review",
                            ticker=row.ticker,
                            thesis_id=row.id,
                            message=msg,
                            urgency=AttentionUrgency.HIGH,
                            ts=last_reviewed or created_at_utc,
                            metadata={"days_overdue": days_overdue},
                        ))
                except Exception as exc:
                    logger.warning("attention.source2_overdue.error", error=str(exc), exc_info=True)

            # ---- Source 3: Upcoming catalysts within 72h -------------------
            if _THESIS_MODELS_AVAILABLE:
                try:
                    cutoff_near = now + timedelta(hours=_UPCOMING_CATALYST_HOURS)

                    for row in (
                        await session.execute(
                            select(
                                Catalyst.id,
                                Catalyst.thesis_id,
                                Catalyst.description,
                                Catalyst.expected_date,
                                Thesis.ticker,
                                Thesis.title,
                            )
                            .join(Thesis, Thesis.id == Catalyst.thesis_id)
                            .where(
                                Thesis.user_id == user_id,
                                Thesis.status == ThesisStatus.ACTIVE,
                                Catalyst.status == CatalystStatus.PENDING,
                                Catalyst.expected_date >= now,
                                Catalyst.expected_date <= cutoff_near,
                            )
                            .order_by(Catalyst.expected_date.asc())
                            .limit(20)
                        )
                    ).all():
                        expected = row.expected_date
                        if expected is not None:
                            expected = _ensure_utc(expected)
                        hours_left = round((expected - now).total_seconds() / 3600, 1) if expected else None
                        desc = (row.description or "")[:80]
                        msg = (
                            f"{row.ticker}: catalyst '{desc}' trong {hours_left}h"
                            if hours_left is not None
                            else f"{row.ticker}: catalyst sắp đến hạn"
                        )
                        _add(AttentionItem(
                            kind="upcoming_catalyst",
                            ticker=row.ticker,
                            thesis_id=row.thesis_id,
                            message=msg,
                            urgency=AttentionUrgency.HIGH,
                            ts=expected or now,
                            metadata={
                                "catalyst_id": row.id,
                                "hours_left": hours_left,
                                "description": row.description,
                            },
                        ))
                except Exception as exc:
                    logger.warning("attention.source3_catalysts.error", error=str(exc), exc_info=True)

            # ---- Source 4: Stop-loss proximity (requires price_map) --------
            if price_map and _THESIS_MODELS_AVAILABLE:
                try:
                    for row in (
                        await session.execute(
                            select(
                                Thesis.id,
                                Thesis.ticker,
                                Thesis.title,
                                Thesis.stop_loss,
                                Thesis.created_at,
                            )
                            .where(
                                Thesis.user_id == user_id,
                                Thesis.status == ThesisStatus.ACTIVE,
                                Thesis.stop_loss != None,  # noqa: E711
                            )
                        )
                    ).all():
                        current = price_map.get(row.ticker)
                        if current is None or row.stop_loss is None or row.stop_loss <= 0:
                            continue
                        distance_pct = abs(current - row.stop_loss) / row.stop_loss * 100
                        if distance_pct <= _STOP_LOSS_PROXIMITY_PCT:
                            _add(AttentionItem(
                                kind="stop_loss_proximity",
                                ticker=row.ticker,
                                thesis_id=row.id,
                                message=(
                                    f"{row.ticker}: giá hiện tại cách stop_loss "
                                    f"{distance_pct:.1f}% "
                                    f"(giá: {current:,.0f} | SL: {row.stop_loss:,.0f})"
                                ),
                                urgency=AttentionUrgency.CRITICAL,
                                ts=now,
                                metadata={
                                    "current_price": current,
                                    "stop_loss": row.stop_loss,
                                    "distance_pct": round(distance_pct, 2),
                                },
                            ))
                except Exception as exc:
                    logger.warning("attention.source4_stoploss.error", error=str(exc), exc_info=True)

        # Sort: critical → high → medium, stable ts desc within tier
        items.sort(key=lambda x: (_URGENCY_ORDER.get(x.urgency, 99), -(x.ts.timestamp())))
        items = items[:limit]

        response = AttentionPanelResponse(
            user_id=user_id,
            generated_at=now,
            items=items,
            total=len(items),
        )
        _cache.set("attention", user_id, response, extra=cache_extra)
        return response
