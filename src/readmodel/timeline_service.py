"""ThesisTimelineService — ordered event log + focused review timeline + conviction timeline.

Owner: readmodel segment.
Builds chronological views from multiple tables.
Read-only. No writes. No AI calls.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.readmodel.event_text import filter_events
from src.readmodel.schemas import (
    ConvictionBreakdown,
    ConvictionPoint,
    ConvictionTimelineResponse,
    ConvictionTrend,
    ReviewTimelineItem,
    ReviewTimelineResponse,
    ThesisTimelineResponse,
    TimelineEvent,
    TimelineEventKind,
)


class ThesisTimelineService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # General event timeline
    # ------------------------------------------------------------------

    async def get_timeline(self, thesis_id: int) -> ThesisTimelineResponse | None:
        from src.thesis.models import (
            Assumption,
            Catalyst,
            CatalystStatus,
            Thesis,
            ThesisReview,
            ThesisSnapshot,
            ThesisStatus,
        )

        thesis_result = await self._session.execute(select(Thesis).where(Thesis.id == thesis_id))
        thesis = thesis_result.scalar_one_or_none()
        if thesis is None:
            return None

        events: list[TimelineEvent] = []

        # 1. Thesis created
        events.append(
            TimelineEvent(
                kind=TimelineEventKind.CREATED,
                ts=thesis.created_at,
                summary=f"Thesis created for {thesis.ticker}: {thesis.title}",
                detail={"entry_price": thesis.entry_price, "target_price": thesis.target_price},
            )
        )

        # 2. AI reviews
        reviews_result = await self._session.execute(
            select(ThesisReview)
            .where(ThesisReview.thesis_id == thesis_id)
            .order_by(ThesisReview.reviewed_at)
        )
        for review in reviews_result.scalars().all():
            events.append(
                TimelineEvent(
                    kind=TimelineEventKind.REVIEWED,
                    ts=review.reviewed_at,
                    summary=f"AI review: {review.verdict} (confidence {review.confidence:.0%})",
                    detail={
                        "verdict": str(review.verdict),
                        "confidence": review.confidence,
                        "risk_signals": review.risk_signals,
                    },
                )
            )

        # 3. Assumption status changes (non-PENDING)
        assumptions_result = await self._session.execute(
            select(Assumption)
            .where(Assumption.thesis_id == thesis_id)
            .order_by(Assumption.updated_at)
        )
        for assumption in assumptions_result.scalars().all():
            from src.thesis.models import AssumptionStatus

            if assumption.status != AssumptionStatus.PENDING:
                events.append(
                    TimelineEvent(
                        kind=TimelineEventKind.ASSUMPTION_UPDATED,
                        ts=assumption.updated_at,
                        summary=f"Assumption '{assumption.description[:60]}' \u2192 {assumption.status}",
                        detail={"assumption_id": assumption.id, "status": str(assumption.status)},
                    )
                )

        # 4. Triggered catalysts
        catalysts_result = await self._session.execute(
            select(Catalyst)
            .where(
                Catalyst.thesis_id == thesis_id,
                Catalyst.status == CatalystStatus.TRIGGERED,
            )
            .order_by(Catalyst.triggered_at)
        )
        for catalyst in catalysts_result.scalars().all():
            if catalyst.triggered_at:
                events.append(
                    TimelineEvent(
                        kind=TimelineEventKind.CATALYST_TRIGGERED,
                        ts=catalyst.triggered_at,
                        summary=f"Catalyst triggered: {catalyst.description[:80]}",
                        detail={"catalyst_id": catalyst.id},
                    )
                )

        # 5. Snapshots (performance checkpoints)
        snapshots_result = await self._session.execute(
            select(ThesisSnapshot)
            .where(ThesisSnapshot.thesis_id == thesis_id)
            .order_by(ThesisSnapshot.snapshotted_at)
        )
        for snap in snapshots_result.scalars().all():
            price_str = f"{snap.price_at_snapshot:,.0f}" if snap.price_at_snapshot is not None else "n/a"
            pnl_str = f"{snap.pnl_pct:+.1f}%" if snap.pnl_pct is not None else "n/a"
            events.append(
                TimelineEvent(
                    kind=TimelineEventKind.SNAPSHOT,
                    ts=snap.snapshotted_at,
                    summary=f"Snapshot @ {price_str} VND | PnL {pnl_str}",
                    detail={
                        "price": snap.price_at_snapshot,
                        "pnl_pct": snap.pnl_pct,
                        "score": snap.score_at_snapshot,
                    },
                )
            )

        # 6. Terminal event (invalidated / closed)
        if thesis.status in (ThesisStatus.INVALIDATED, ThesisStatus.CLOSED):
            ts = thesis.closed_at or thesis.updated_at
            events.append(
                TimelineEvent(
                    kind=str(thesis.status.value),
                    ts=ts,
                    summary=f"Thesis {thesis.status.value}: {thesis.ticker}",
                    detail={"final_score": thesis.score},
                )
            )

        # filter_events: drop null/empty + keep 30 latest, oldest → newest
        return ThesisTimelineResponse(
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            title=thesis.title,
            events=filter_events(events),
        )

    # ------------------------------------------------------------------
    # Focused review timeline — 5 AI reviews gần nhất
    # ------------------------------------------------------------------

    async def get_review_timeline(
        self,
        thesis_id: int,
        limit: int = 5,
    ) -> ReviewTimelineResponse | None:
        """Trả về `limit` AI reviews gần nhất của một thesis, mới nhất trước.

        Chỉ query ThesisReview — không join bảng khác.
        risk_signals / next_watch_items được parse từ JSON string với fallback [].
        """
        from src.thesis.models import Thesis, ThesisReview

        thesis_result = await self._session.execute(
            select(Thesis).where(Thesis.id == thesis_id)
        )
        thesis = thesis_result.scalar_one_or_none()
        if thesis is None:
            return None

        reviews_result = await self._session.execute(
            select(ThesisReview)
            .where(ThesisReview.thesis_id == thesis_id)
            .order_by(ThesisReview.reviewed_at.desc())
            .limit(limit)
        )
        reviews = reviews_result.scalars().all()

        items: list[ReviewTimelineItem] = []
        for r in reviews:
            risk_signals = _parse_json_list(r.risk_signals)
            next_watch_items = _parse_json_list(r.next_watch_items)
            confidence = float(r.confidence or 0)
            items.append(
                ReviewTimelineItem(
                    review_id=r.id,
                    reviewed_at=r.reviewed_at,
                    verdict=str(r.verdict.value) if hasattr(r.verdict, "value") else str(r.verdict),
                    confidence=confidence,
                    confidence_pct=round(confidence * 100),
                    reasoning=r.reasoning or None,
                    risk_signals=risk_signals,
                    next_watch_items=next_watch_items,
                    reviewed_price=r.reviewed_price,
                )
            )

        return ReviewTimelineResponse(
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            title=thesis.title,
            items=items,
            total=len(items),
        )

    # ------------------------------------------------------------------
    # Conviction Score Timeline
    # ------------------------------------------------------------------

    async def get_conviction_timeline(
        self,
        thesis_id: int,
        limit: int = 20,
    ) -> ConvictionTimelineResponse | None:
        """Trả về cỗ conviction score theo thời gian cho một thesis.

        Mỗi point ứng với một ThesisSnapshot. Verdict + confidence được lấy
        từ ThesisReview gần nhất trước (hoặc đúng bằng) snapshotted_at.

        Score coalesce: score_at_snapshot (scheduler snapshots) OR score
        (review-triggered snapshots). Rows where both are NULL are skipped.

        Breakdown được parse từ score_breakdown JSON column (nullable cho legacy rows).
        Trend = so sánh avg(3 điểm đầu) vs avg(3 điểm cuối); Δ > 5 → improving,
        Δ < -5 → declining, else stable. < 2 điểm → insufficient_data.
        """
        from src.thesis.models import Thesis, ThesisReview, ThesisSnapshot
        from src.thesis.scoring_service import score_tier

        thesis_result = await self._session.execute(
            select(Thesis).where(Thesis.id == thesis_id)
        )
        thesis = thesis_result.scalar_one_or_none()
        if thesis is None:
            return None

        # --- Load snapshots (oldest first) ---
        snaps_result = await self._session.execute(
            select(ThesisSnapshot)
            .where(ThesisSnapshot.thesis_id == thesis_id)
            .order_by(ThesisSnapshot.snapshotted_at.asc())
            .limit(limit)
        )
        snapshots = snaps_result.scalars().all()

        # --- Load all reviews for this thesis (needed for nearest-prior lookup) ---
        reviews_result = await self._session.execute(
            select(ThesisReview)
            .where(ThesisReview.thesis_id == thesis_id)
            .order_by(ThesisReview.reviewed_at.asc())
        )
        reviews = reviews_result.scalars().all()

        # Build points
        points: list[ConvictionPoint] = []
        for snap in snapshots:
            score = snap.score_at_snapshot if snap.score_at_snapshot is not None else snap.score
            if score is None:
                continue

            tier_label, tier_icon = score_tier(score)
            breakdown = _parse_breakdown(snap.score_breakdown)

            nearest = _nearest_prior_review(reviews, snap.snapshotted_at)
            verdict = None
            confidence = None
            if nearest is not None:
                verdict = (
                    str(nearest.verdict.value)
                    if hasattr(nearest.verdict, "value")
                    else str(nearest.verdict)
                )
                confidence = float(nearest.confidence or 0)

            points.append(
                ConvictionPoint(
                    snapshot_id=snap.id,
                    snapshotted_at=snap.snapshotted_at,
                    score=score,
                    score_tier=tier_label,
                    score_tier_icon=tier_icon,
                    breakdown=breakdown,
                    verdict=verdict,
                    confidence=confidence,
                    price=snap.price_at_snapshot,
                    pnl_pct=snap.pnl_pct,
                )
            )

        trend = _compute_trend(points)
        latest_score = points[-1].score if points else None
        earliest_score = points[0].score if len(points) >= 2 else None

        return ConvictionTimelineResponse(
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            title=thesis.title,
            points=points,
            trend=trend,
            latest_score=latest_score,
            earliest_score=earliest_score,
            total=len(points),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json_list(raw: str | None) -> list[str]:
    """Parse JSON string → list[str]. Returns [] on null / malformed input."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        return []
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _parse_breakdown(raw: str | None) -> ConvictionBreakdown | None:
    """Parse score_breakdown JSON column → ConvictionBreakdown. None on legacy/null."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return ConvictionBreakdown(
            assumption_health=float(data.get("assumption_health", 0)),
            catalyst_progress=float(data.get("catalyst_progress", 0)),
            risk_reward=float(data.get("risk_reward", 0)),
            review_confidence=float(data.get("review_confidence", 0)),
        )
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return None


def _nearest_prior_review(reviews: list, snapshot_ts) -> object | None:  # type: ignore[type-arg]
    """Return review with reviewed_at <= snapshot_ts, latest first. O(n) scan.

    reviews must be sorted ascending by reviewed_at (caller guarantees this).
    """
    best = None
    for r in reviews:
        if r.reviewed_at <= snapshot_ts:
            best = r
        else:
            break
    return best


def _compute_trend(points: list[ConvictionPoint]) -> str:
    """Compare avg score of first-3 vs last-3 data points.

    Δ > +5  → improving
    Δ < -5  → declining
    else    → stable
    < 2 pts → insufficient_data
    """
    if len(points) < 2:
        return ConvictionTrend.INSUFFICIENT_DATA

    window = min(3, len(points) // 2 or 1)
    early_avg = sum(p.score for p in points[:window]) / window
    late_avg = sum(p.score for p in points[-window:]) / window
    delta = late_avg - early_avg

    if delta > 5:
        return ConvictionTrend.IMPROVING
    if delta < -5:
        return ConvictionTrend.DECLINING
    return ConvictionTrend.STABLE
