"""ThesisTimelineService — ordered event log + focused review timeline + conviction timeline.

Owner: readmodel segment.
Builds chronological views from multiple tables.
Read-only. No writes. No AI calls.
"""

from __future__ import annotations

import json
from datetime import timedelta

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

_REASONING_SUMMARY_MAX = 200  # chars truncated for conviction drawer

# Wave 3: tolerance windows for review-to-snapshot matching.
# Raise REVIEWED_KIND_TOLERANCE_SECS if scheduler + review jitter is larger.
_NEAREST_REVIEW_LOOKAHEAD_SECS: int = 14400   # 4 h — catch reviews that run after snapshot
_REVIEWED_KIND_TOLERANCE_SECS: int = 300      # 5 min — mark point as 'reviewed' kind


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

        # filter_events: drop null/empty + keep 30 latest, oldest -> newest
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
        current_price: float | None = None,
    ) -> ConvictionTimelineResponse | None:
        """Trả về cỗ conviction score theo thời gian cho một thesis.

        Mỗi point ứng với một ThesisSnapshot. Verdict + confidence được lấy
        từ ThesisReview gần nhất trước (hoặc đúng bằng) snapshotted_at.

        Score coalesce: score_at_snapshot (scheduler snapshots) OR score
        (review-triggered snapshots). Rows where both are NULL are skipped.

        Breakdown được parse từ score_breakdown JSON column (nullable cho legacy rows).
        Trend = so sánh avg(3 điểm đầu) vs avg(3 điểm cuối); Δ > 5 → improving,
        Δ < -5 → declining, else stable. < 2 điểm → insufficient_data.

        kind:
          'reviewed'  — snapshot has a co-occurring review within
                        _REVIEWED_KIND_TOLERANCE_SECS (default 5 min).
          'snapshot'  — regular scheduler snapshot.

        Wave 3 — review lookahead:
          _nearest_prior_review() now also considers reviews up to
          _NEAREST_REVIEW_LOOKAHEAD_SECS (default 4 h) AFTER snapshotted_at.
          This fixes the race condition where the AI review job fires a few
          seconds after the scheduler snapshot, causing the snapshot point
          to incorrectly inherit the previous (stale) review.

        reasoning_summary: first _REASONING_SUMMARY_MAX chars of nearest.reasoning.
        risk_signals: parsed list from nearest.risk_signals JSON.
        entry_price: exposed from Thesis.entry_price for price chart annotation.

        Ordering: fetch `limit` NEWEST snapshots (desc), then reverse to chronological
        (asc) so the chart always shows the most recent data without truncating old-first.

        Price gap handling:
          Option B — forward-fill: if price_at_snapshot is None on a point, carry forward
            the last known price so the price dataset length always matches conviction.
            Points filled this way are marked with price_filled=True.
          Option C — live price fallback: if the last point still has no price after
            forward-fill, and current_price is provided by the caller (e.g. from a live
            quote fetched by the API/bot adapter), it is injected into that last point.
            This covers the edge case where today's AI review ran before the market
            snapshot job.
            current_price does NOT propagate backwards — only the last point is affected.
        """
        from src.thesis.models import Thesis, ThesisReview, ThesisSnapshot
        from src.thesis.scoring_service import score_tier

        thesis_result = await self._session.execute(
            select(Thesis).where(Thesis.id == thesis_id)
        )
        thesis = thesis_result.scalar_one_or_none()
        if thesis is None:
            return None

        # --- Load the N most-recent snapshots, then reverse to oldest-first for chart ---
        snaps_result = await self._session.execute(
            select(ThesisSnapshot)
            .where(ThesisSnapshot.thesis_id == thesis_id)
            .order_by(ThesisSnapshot.snapshotted_at.desc())
            .limit(limit)
        )
        snapshots = list(reversed(snaps_result.scalars().all()))

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

            nearest = _nearest_prior_review(
                reviews,
                snap.snapshotted_at,
                lookahead_secs=_NEAREST_REVIEW_LOOKAHEAD_SECS,
            )
            verdict = None
            confidence = None
            reasoning_summary = None
            risk_signals: list[str] = []
            kind = "snapshot"

            if nearest is not None:
                verdict = (
                    str(nearest.verdict.value)
                    if hasattr(nearest.verdict, "value")
                    else str(nearest.verdict)
                )
                confidence = float(nearest.confidence or 0)
                reasoning_summary = _truncate(nearest.reasoning, _REASONING_SUMMARY_MAX)
                risk_signals = _parse_json_list(nearest.risk_signals)
                # Mark as 'reviewed' if review co-occurs within tolerance window of snapshot
                if abs((nearest.reviewed_at - snap.snapshotted_at).total_seconds()) <= _REVIEWED_KIND_TOLERANCE_SECS:
                    kind = "reviewed"

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
                    kind=kind,
                    reasoning_summary=reasoning_summary,
                    risk_signals=risk_signals,
                )
            )

        # ------------------------------------------------------------------
        # Option B: forward-fill price gaps across all points
        # ------------------------------------------------------------------
        last_known_price: float | None = None
        for point in points:
            if point.price is not None:
                last_known_price = point.price
            elif last_known_price is not None:
                point.price = last_known_price
                point.price_filled = True  # type: ignore[attr-defined]

        # ------------------------------------------------------------------
        # Option C: live price fallback for the last point only
        # ------------------------------------------------------------------
        if points and points[-1].price is None and current_price is not None:
            points[-1].price = current_price
            points[-1].price_filled = True  # type: ignore[attr-defined]

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
            entry_price=thesis.entry_price,
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


def _truncate(text: str | None, max_chars: int) -> str | None:
    """Truncate string to max_chars, appending '…' if truncated. Returns None if empty."""
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    return text[:max_chars] + ("\u2026" if len(text) > max_chars else "")


def _nearest_prior_review(
    reviews: list,  # type: ignore[type-arg]
    snapshot_ts,
    lookahead_secs: int = 14400,
) -> object | None:  # type: ignore[type-arg]
    """Return the review closest to snapshot_ts within the search window.

    Search window: [snapshot_ts - ∞, snapshot_ts + lookahead_secs].

    Strategy:
      1. Prefer the latest review with reviewed_at <= snapshot_ts (prior review).
      2. If no prior review exists, fall back to the earliest review within
         lookahead_secs AFTER snapshot_ts (covers scheduler/review race condition).

    reviews must be sorted ascending by reviewed_at (caller guarantees this).

    Args:
        reviews:        All ThesisReview rows for the thesis, asc by reviewed_at.
        snapshot_ts:    The snapshotted_at timestamp of the conviction point.
        lookahead_secs: How far forward (in seconds) to look for a review when
                        no prior review exists. Default: 14400 (4 h).
    """
    lookahead = timedelta(seconds=lookahead_secs)
    best_prior: object | None = None
    first_after: object | None = None

    for r in reviews:
        if r.reviewed_at <= snapshot_ts:
            best_prior = r
        elif first_after is None and (r.reviewed_at - snapshot_ts) <= lookahead:
            first_after = r
            # No break — keep scanning to update best_prior for any remaining prior reviews.
            # Once first_after is set we still need to find the true latest prior review.

    return best_prior if best_prior is not None else first_after


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
