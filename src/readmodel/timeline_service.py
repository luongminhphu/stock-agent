"""ThesisTimelineService — ordered event log for a single thesis.

Owner: readmodel segment.
Builds a chronological list of significant events from multiple tables.
Read-only. No writes. No AI calls.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.readmodel.schemas import (
    ThesisTimelineResponse,
    TimelineEvent,
    TimelineEventKind,
)


class ThesisTimelineService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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

        # Load thesis
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
                        summary=f"Assumption '{assumption.description[:60]}' → {assumption.status}",
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
            pnl_str = f"{snap.pnl_pct:+.1f}%" if snap.pnl_pct is not None else "n/a"
            events.append(
                TimelineEvent(
                    kind=TimelineEventKind.SNAPSHOT,
                    ts=snap.snapshotted_at,
                    summary=f"Snapshot @ {snap.price_at_snapshot:,.0f} VND | PnL {pnl_str}",
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

        # Sort all events chronologically
        events.sort(key=lambda e: e.ts)

        return ThesisTimelineResponse(
            thesis_id=thesis.id,
            ticker=thesis.ticker,
            title=thesis.title,
            events=events,
        )
