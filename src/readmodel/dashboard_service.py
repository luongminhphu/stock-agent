"""DashboardService — optimised read queries for the user dashboard.

Owner: readmodel segment.

Endpoints served (via src/api/routes/readmodel.py):
    get_stats()                  — KPI tong quan (open theses, verdict dist, risky count)
    get_theses_list()            — list thesis + last review + health score
    get_thesis_detail()          — full detail + assumption history + score series
    get_upcoming_catalysts()     — catalysts sap toi
    get_scan_latest()            — snapshot scan gan nhat (WatchlistScan)
    get_brief_latest()           — snapshot brief gan nhat (BriefSnapshot)
    get_verdict_accuracy()       — backtesting: accuracy per verdict
    get_thesis_performances()    — backtesting: performance per thesis
    get_price_snapshots()        — backtesting: price chart data for one thesis

Design rules:
- SELECT only columns needed; never load full ORM graphs.
- No writes. No business logic. No AI calls.
- Scoring logic delegates to src.thesis.scoring_service (not duplicated here).
- All public methods are async and accept an AsyncSession.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Integer, and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.readmodel.schemas import (
    DashboardResponse,
    ThesisSummaryRow,
    WatchlistSnapshotRow,
)
from src.thesis.scoring_service import score_tier

logger = get_logger(__name__)

# Vietnam timezone offset (UTC+7)
_VN_OFFSET = timedelta(hours=7)


def _now_vn() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(_VN_OFFSET))


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # 1. Stats — KPI tong quan
    # ------------------------------------------------------------------

    async def get_stats(self, user_id: str) -> dict[str, Any]:
        from src.thesis.models import (
            Catalyst,
            CatalystStatus,
            Thesis,
            ThesisReview,
            ThesisStatus,
        )

        open_count = (
            await self._session.scalar(
                select(func.count(Thesis.id)).where(
                    Thesis.user_id == user_id,
                    Thesis.status == ThesisStatus.ACTIVE,
                )
            )
            or 0
        )

        latest_review_subq = (
            select(
                ThesisReview.thesis_id,
                ThesisReview.verdict,
                func.row_number()
                .over(
                    partition_by=ThesisReview.thesis_id,
                    order_by=ThesisReview.reviewed_at.desc(),
                )
                .label("rn"),
            )
            .join(Thesis, Thesis.id == ThesisReview.thesis_id)
            .where(
                Thesis.user_id == user_id,
                Thesis.status == ThesisStatus.ACTIVE,
            )
            .subquery()
        )
        verdict_rows = (
            await self._session.execute(
                select(
                    latest_review_subq.c.verdict,
                    func.count().label("cnt"),
                )
                .where(latest_review_subq.c.rn == 1)
                .group_by(latest_review_subq.c.verdict)
            )
        ).all()
        verdict_map: dict[str, int] = {str(r.verdict): r.cnt for r in verdict_rows}

        now_vn = _now_vn()
        in_7d = now_vn + timedelta(days=7)
        upcoming_7d = (
            await self._session.scalar(
                select(func.count(Catalyst.id))
                .join(Thesis, Thesis.id == Catalyst.thesis_id)
                .where(
                    Thesis.user_id == user_id,
                    Thesis.status == ThesisStatus.ACTIVE,
                    Catalyst.status == CatalystStatus.PENDING,
                    Catalyst.expected_date.between(now_vn, in_7d),
                )
            )
            or 0
        )

        total_reviews = (
            await self._session.scalar(
                select(func.count(ThesisReview.id))
                .join(Thesis, Thesis.id == ThesisReview.thesis_id)
                .where(Thesis.user_id == user_id)
            )
            or 0
        )

        today_start = now_vn.replace(hour=0, minute=0, second=0, microsecond=0)
        reviews_today = (
            await self._session.scalar(
                select(func.count(ThesisReview.id))
                .join(Thesis, Thesis.id == ThesisReview.thesis_id)
                .where(
                    Thesis.user_id == user_id,
                    ThesisReview.reviewed_at >= today_start,
                )
            )
            or 0
        )

        risky = (
            await self._session.scalar(
                select(func.count(Thesis.id)).where(
                    Thesis.user_id == user_id,
                    Thesis.status == ThesisStatus.ACTIVE,
                    Thesis.score < 40,
                )
            )
            or 0
        )

        return {
            "open_theses": open_count,
            "verdict": {
                "BULLISH": verdict_map.get("BULLISH", 0),
                "BEARISH": verdict_map.get("BEARISH", 0),
                "NEUTRAL": verdict_map.get("NEUTRAL", 0),
                "WATCHLIST": verdict_map.get("WATCHLIST", 0),
            },
            "risky_theses": risky,
            "upcoming_catalysts_7d": upcoming_7d,
            "total_reviews": total_reviews,
            "reviews_today": reviews_today,
        }

    # ------------------------------------------------------------------
    # 2. Theses list
    # ------------------------------------------------------------------

    async def get_theses_list(
        self,
        user_id: str,
        status: str | None = "active",
        ticker: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        from src.thesis.models import (
            Assumption,
            Catalyst,
            Thesis,
            ThesisReview,
            ThesisStatus,
        )

        filters = [Thesis.user_id == user_id]
        if status and status != "all":
            try:
                filters.append(Thesis.status == ThesisStatus(status))
            except ValueError:
                pass
        if ticker:
            filters.append(Thesis.ticker == ticker.upper())

        latest_review_subq = select(
            ThesisReview.thesis_id,
            ThesisReview.verdict,
            ThesisReview.confidence,
            ThesisReview.reasoning,
            ThesisReview.reviewed_at,
            func.row_number()
            .over(
                partition_by=ThesisReview.thesis_id,
                order_by=ThesisReview.reviewed_at.desc(),
            )
            .label("rn"),
        ).subquery()

        n_assumptions_subq = (
            select(
                Assumption.thesis_id,
                func.count(Assumption.id).label("n"),
            )
            .group_by(Assumption.thesis_id)
            .subquery()
        )
        n_catalysts_subq = (
            select(
                Catalyst.thesis_id,
                func.count(Catalyst.id).label("n"),
            )
            .group_by(Catalyst.thesis_id)
            .subquery()
        )

        stmt = (
            select(
                Thesis,
                latest_review_subq.c.verdict.label("last_verdict"),
                latest_review_subq.c.confidence.label("last_confidence"),
                latest_review_subq.c.reviewed_at.label("last_reviewed_at"),
                func.coalesce(n_assumptions_subq.c.n, 0).label("n_assumptions"),
                func.coalesce(n_catalysts_subq.c.n, 0).label("n_catalysts"),
            )
            .outerjoin(
                latest_review_subq,
                and_(
                    latest_review_subq.c.thesis_id == Thesis.id,
                    latest_review_subq.c.rn == 1,
                ),
            )
            .outerjoin(n_assumptions_subq, n_assumptions_subq.c.thesis_id == Thesis.id)
            .outerjoin(n_catalysts_subq, n_catalysts_subq.c.thesis_id == Thesis.id)
            .where(*filters)
            .order_by(Thesis.updated_at.desc())
            .limit(limit)
        )

        rows = (await self._session.execute(stmt)).all()
        result = []
        for r in rows:
            t = r.Thesis
            tier_label, tier_icon = score_tier(t.score) if t.score is not None else (None, None)
            result.append(
                {
                    "id": t.id,
                    "ticker": t.ticker,
                    "title": t.title,
                    "status": str(t.status.value),
                    "score": t.score,
                    "score_tier": tier_label,
                    "score_tier_icon": tier_icon,
                    "entry_price": t.entry_price,
                    "target_price": t.target_price,
                    "stop_loss": t.stop_loss,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                    "last_verdict": str(r.last_verdict) if r.last_verdict else None,
                    "last_confidence": r.last_confidence,
                    "last_reviewed_at": r.last_reviewed_at.isoformat()
                    if r.last_reviewed_at
                    else None,
                    "n_assumptions": r.n_assumptions,
                    "n_catalysts": r.n_catalysts,
                }
            )
        return result

    # ------------------------------------------------------------------
    # 3. Thesis detail
    # ------------------------------------------------------------------

    async def get_thesis_detail(self, user_id: str, thesis_id: int) -> dict[str, Any] | None:
        from src.thesis.models import (
            Assumption,
            Catalyst,
            Thesis,
            ThesisReview,
        )

        thesis = (
            await self._session.execute(
                select(Thesis).where(Thesis.id == thesis_id)
            )
        ).scalar_one_or_none()
        if thesis is None or thesis.user_id != user_id:
            return None

        # FIX(Wave 5a): SELECT explicit columns only — avoids triggering the
        # selectin `recommendations` relationship on ThesisReview, which would
        # fire 1 extra query per review row (N+1).
        reviews_rows = (
            await self._session.execute(
                select(
                    ThesisReview.id,
                    ThesisReview.verdict,
                    ThesisReview.confidence,
                    ThesisReview.reasoning,
                    ThesisReview.risk_signals,
                    ThesisReview.next_watch_items,
                    ThesisReview.reviewed_at,
                    ThesisReview.reviewed_price,
                )
                .where(ThesisReview.thesis_id == thesis_id)
                .order_by(ThesisReview.reviewed_at.desc())
                .limit(20)
            )
        ).all()

        assumptions_rows = (
            (
                await self._session.execute(
                    select(Assumption)
                    .where(Assumption.thesis_id == thesis_id)
                    .order_by(Assumption.id.asc())
                )
            )
            .scalars()
            .all()
        )

        catalysts_rows = (
            (
                await self._session.execute(
                    select(Catalyst)
                    .where(Catalyst.thesis_id == thesis_id)
                    .order_by(Catalyst.expected_date.asc())
                )
            )
            .scalars()
            .all()
        )

        def _review_dict(r: Any) -> dict:
            return {
                "id": r.id,
                "verdict": str(r.verdict.value) if hasattr(r.verdict, "value") else str(r.verdict),
                "confidence": r.confidence,
                "reasoning": r.reasoning,
                "risk_signals": _parse_json_field(r.risk_signals),
                "next_watch_items": _parse_json_field(r.next_watch_items),
                "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                "reviewed_price": r.reviewed_price,
            }

        def _assumption_dict(a: Assumption) -> dict:
            return {
                "id": a.id,
                "description": a.description,
                "status": str(a.status.value),
                "note": a.note,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            }

        def _catalyst_dict(c: Catalyst) -> dict:
            return {
                "id": c.id,
                "description": c.description,
                "status": str(c.status.value),
                "expected_date": c.expected_date.isoformat() if c.expected_date else None,
                "triggered_at": c.triggered_at.isoformat() if c.triggered_at else None,
                "note": c.note,
            }

        last_review = reviews_rows[0] if reviews_rows else None
        tier_label, tier_icon = score_tier(thesis.score) if thesis.score is not None else (None, None)

        return {
            "thesis": {
                "id": thesis.id,
                "ticker": thesis.ticker,
                "title": thesis.title,
                "summary": thesis.summary,
                "status": str(thesis.status.value),
                "score": thesis.score,
                "score_tier": tier_label,
                "score_tier_icon": tier_icon,
                "entry_price": thesis.entry_price,
                "target_price": thesis.target_price,
                "stop_loss": thesis.stop_loss,
                "created_at": thesis.created_at.isoformat() if thesis.created_at else None,
                "updated_at": thesis.updated_at.isoformat() if thesis.updated_at else None,
                "last_verdict": str(last_review.verdict.value if hasattr(last_review.verdict, "value") else last_review.verdict) if last_review else None,
                "last_confidence": last_review.confidence if last_review else None,
                "n_assumptions": len(assumptions_rows),
                "n_catalysts": len(catalysts_rows),
                "n_reviews": len(reviews_rows),
            },
            "reviews": [_review_dict(r) for r in reviews_rows],
            "assumptions": [_assumption_dict(a) for a in assumptions_rows],
            "catalysts": [_catalyst_dict(c) for c in catalysts_rows],
        }

    # ------------------------------------------------------------------
    # 4. Upcoming catalysts
    # ------------------------------------------------------------------

    async def get_upcoming_catalysts(self, user_id: str, days: int = 30) -> list[dict[str, Any]]:
        from src.thesis.models import Catalyst, CatalystStatus, Thesis, ThesisStatus

        now_vn = _now_vn()
        end = now_vn + timedelta(days=days)

        rows = (
            await self._session.execute(
                select(
                    Catalyst.id,
                    Catalyst.thesis_id,
                    Catalyst.description,
                    Catalyst.expected_date,
                    Catalyst.note,
                    Thesis.ticker.label("thesis_ticker"),
                    Thesis.title.label("thesis_title"),
                    Thesis.status.label("thesis_status"),
                )
                .join(Thesis, Thesis.id == Catalyst.thesis_id)
                .where(
                    Thesis.user_id == user_id,
                    Thesis.status == ThesisStatus.ACTIVE,
                    Catalyst.status == CatalystStatus.PENDING,
                    Catalyst.expected_date.between(now_vn, end),
                )
                .order_by(Catalyst.expected_date.asc())
                .limit(100)
            )
        ).all()

        return [
            {
                "id": r.id,
                "thesis_id": r.thesis_id,
                "description": r.description,
                "expected_date": r.expected_date.isoformat() if r.expected_date else None,
                "note": r.note,
                "thesis_ticker": r.thesis_ticker,
                "thesis_title": r.thesis_title,
                "thesis_status": str(r.thesis_status.value),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 5. Latest scan snapshot
    # ------------------------------------------------------------------

    async def get_scan_latest(self, user_id: str) -> dict[str, Any] | None:
        # FIX(Wave 5a): catch specific exceptions — ImportError when watchlist
        # models are not yet migrated, and log warnings instead of silent swallow.
        try:
            from src.watchlist.models import WatchlistScan
        except ImportError:
            logger.warning("get_scan_latest.import_error", detail="WatchlistScan model not available")
            return None

        try:
            row = (
                await self._session.execute(
                    select(WatchlistScan)
                    .where(WatchlistScan.user_id == user_id)
                    .order_by(WatchlistScan.scanned_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if not row:
                return None

            return {
                "id": row.id,
                "user_id": row.user_id,
                "summary": row.summary,
                "scanned_at": row.scanned_at.isoformat() if row.scanned_at else None,
            }
        except Exception as exc:
            logger.warning("get_scan_latest.db_error", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # 6. Latest brief snapshot
    # ------------------------------------------------------------------

    async def get_brief_latest(self, user_id: str, phase: str = "morning") -> dict[str, Any] | None:
        # FIX(Wave 5a): same pattern — split ImportError from runtime DB errors,
        # log both instead of silently returning None.
        try:
            from src.briefing.models import BriefSnapshot
        except ImportError:
            logger.warning("get_brief_latest.import_error", detail="BriefSnapshot model not available")
            return None

        try:
            row = (
                await self._session.execute(
                    select(BriefSnapshot)
                    .where(
                        BriefSnapshot.user_id == user_id,
                        BriefSnapshot.phase == phase,
                    )
                    .order_by(BriefSnapshot.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if not row:
                return None

            return {
                "id": row.id,
                "user_id": row.user_id,
                "phase": row.phase,
                "content": row.content,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        except Exception as exc:
            logger.warning("get_brief_latest.db_error", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # 7. Backtesting — verdict accuracy  (pure-Python aggregation)
    # ------------------------------------------------------------------

    async def get_verdict_accuracy(self, user_id: str) -> list[dict[str, Any]]:
        from src.thesis.models import Thesis, ThesisReview, ThesisSnapshot

        latest_review_subq = (
            select(
                ThesisReview.thesis_id,
                ThesisReview.verdict,
                func.row_number()
                .over(
                    partition_by=ThesisReview.thesis_id,
                    order_by=ThesisReview.reviewed_at.desc(),
                )
                .label("rn"),
            )
            .join(Thesis, Thesis.id == ThesisReview.thesis_id)
            .where(Thesis.user_id == user_id)
            .subquery()
        )

        review_rows = (
            await self._session.execute(
                select(
                    latest_review_subq.c.thesis_id,
                    latest_review_subq.c.verdict,
                ).where(latest_review_subq.c.rn == 1)
            )
        ).all()

        thesis_verdict: dict[int, str] = {
            r.thesis_id: str(r.verdict) for r in review_rows
        }

        if not thesis_verdict:
            return []

        snap_rows = (
            await self._session.execute(
                select(
                    ThesisSnapshot.thesis_id,
                    ThesisSnapshot.pnl_pct,
                )
                .join(Thesis, Thesis.id == ThesisSnapshot.thesis_id)
                .where(
                    Thesis.user_id == user_id,
                    ThesisSnapshot.pnl_pct.isnot(None),
                )
            )
        ).all()

        stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "hits": 0, "pnl_sum": 0.0})

        for snap in snap_rows:
            verdict = thesis_verdict.get(snap.thesis_id)
            if verdict is None:
                continue
            pnl = snap.pnl_pct
            bucket = stats[verdict]
            bucket["total"] += 1
            bucket["pnl_sum"] += pnl
            if verdict in ("BULLISH", "WATCHLIST") and pnl >= 0:
                bucket["hits"] += 1
            elif verdict == "BEARISH" and pnl < 0:
                bucket["hits"] += 1

        result = []
        for verdict in sorted(stats.keys()):
            b = stats[verdict]
            total = b["total"]
            avg_pnl = round(b["pnl_sum"] / total, 2) if total else None
            accuracy_pct = None if verdict == "NEUTRAL" else (
                round(b["hits"] / total * 100, 2) if total else None
            )
            result.append(
                {
                    "verdict": verdict,
                    "total": total,
                    "avg_pnl": avg_pnl,
                    "accuracy_pct": accuracy_pct,
                }
            )

        return result

    # ------------------------------------------------------------------
    # 8. Backtesting — thesis performances  (pure-Python aggregation)
    # ------------------------------------------------------------------

    async def get_thesis_performances(
        self,
        user_id: str,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        from src.thesis.models import Thesis, ThesisSnapshot

        filters = [Thesis.user_id == user_id]
        if ticker:
            filters.append(Thesis.ticker == ticker.upper())

        thesis_rows = (
            await self._session.execute(
                select(
                    Thesis.id,
                    Thesis.ticker,
                    Thesis.title,
                    Thesis.status,
                    Thesis.entry_price,
                    Thesis.score,
                )
                .where(*filters)
                .order_by(Thesis.updated_at.desc())
                .limit(min(limit, 500))
            )
        ).all()

        if not thesis_rows:
            return []

        thesis_ids = [r.id for r in thesis_rows]
        thesis_map = {r.id: r for r in thesis_rows}

        snap_rows = (
            await self._session.execute(
                select(
                    ThesisSnapshot.thesis_id,
                    ThesisSnapshot.pnl_pct,
                    ThesisSnapshot.snapshotted_at,
                )
                .where(
                    ThesisSnapshot.thesis_id.in_(thesis_ids),
                    ThesisSnapshot.pnl_pct.isnot(None),
                )
            )
        ).all()

        agg: dict[int, dict] = defaultdict(
            lambda: {"pnl_values": [], "last_snapshot_at": None}
        )

        for s in snap_rows:
            bucket = agg[s.thesis_id]
            bucket["pnl_values"].append(s.pnl_pct)
            if bucket["last_snapshot_at"] is None or s.snapshotted_at > bucket["last_snapshot_at"]:
                bucket["last_snapshot_at"] = s.snapshotted_at

        result = []
        for thesis_id in thesis_ids:
            t = thesis_map[thesis_id]
            b = agg[thesis_id]
            pnl_vals = b["pnl_values"]
            n = len(pnl_vals)
            last_at = b["last_snapshot_at"]

            avg_pnl = round(sum(pnl_vals) / n, 2) if n else None
            max_pnl = round(max(pnl_vals), 2) if n else None
            min_pnl = round(min(pnl_vals), 2) if n else None

            result.append(
                {
                    "thesis_id": t.id,
                    "ticker": t.ticker,
                    "title": t.title,
                    "thesis_status": str(t.status.value),
                    "entry_price": t.entry_price,
                    "score": t.score,
                    "snapshot_count": n,
                    "avg_pnl_pct": avg_pnl,
                    "max_pnl_pct": max_pnl,
                    "min_pnl_pct": min_pnl,
                    "last_snapshot_at": last_at.isoformat() if last_at else None,
                }
            )

        return result

    # ------------------------------------------------------------------
    # 9. Backtesting — price snapshots (chart data)
    # ------------------------------------------------------------------

    async def get_price_snapshots(self, user_id: str, thesis_id: int) -> dict[str, Any] | None:
        from src.thesis.models import Thesis, ThesisReview, ThesisSnapshot

        thesis = (
            await self._session.execute(
                select(Thesis).where(Thesis.id == thesis_id)
            )
        ).scalar_one_or_none()
        if thesis is None or thesis.user_id != user_id:
            return None

        snapshots = (
            (
                await self._session.execute(
                    select(ThesisSnapshot)
                    .where(ThesisSnapshot.thesis_id == thesis_id)
                    .order_by(ThesisSnapshot.snapshotted_at.asc())
                )
            )
            .scalars()
            .all()
        )

        reviews = (
            (
                await self._session.execute(
                    select(ThesisReview)
                    .where(ThesisReview.thesis_id == thesis_id)
                    .order_by(ThesisReview.reviewed_at.asc())
                )
            )
            .scalars()
            .all()
        )

        def _verdict_at(snap_time: datetime) -> str | None:
            last: str | None = None
            for rv in reviews:
                if rv.reviewed_at <= snap_time:
                    last = str(rv.verdict.value)
                else:
                    break
            return last

        return {
            "thesis_id": thesis_id,
            "ticker": thesis.ticker,
            "title": thesis.title,
            "snapshots": [
                {
                    "id": s.id,
                    "price_at_snapshot": s.price_at_snapshot,
                    "pnl_pct": s.pnl_pct,
                    "score_at_snapshot": s.score_at_snapshot,
                    "verdict_at_snap": _verdict_at(s.snapshotted_at) if s.snapshotted_at else None,
                    "snapshotted_at": s.snapshotted_at.isoformat() if s.snapshotted_at else None,
                }
                for s in snapshots
            ],
        }

    # ------------------------------------------------------------------
    # Legacy compatibility
    # ------------------------------------------------------------------

    async def get_dashboard(self, user_id: str) -> DashboardResponse:
        rows = await self._thesis_summary_rows(user_id)
        active = sum(1 for r in rows if r.status == "active")
        invalidated = sum(1 for r in rows if r.status == "invalidated")
        closed = sum(1 for r in rows if r.status == "closed")
        scores = [r.score for r in rows if r.score is not None]
        avg_score = sum(scores) / len(scores) if scores else None
        return DashboardResponse(
            user_id=user_id,
            generated_at=datetime.now(timezone.utc),
            total_theses=len(rows),
            active_count=active,
            invalidated_count=invalidated,
            closed_count=closed,
            avg_score=avg_score,
            theses=rows,
        )

    async def get_watchlist_snapshot(self, user_id: str) -> list[WatchlistSnapshotRow]:
        from src.thesis.models import Thesis
        from src.watchlist.models import WatchlistItem

        stmt = (
            select(
                WatchlistItem.ticker,
                WatchlistItem.note,
                WatchlistItem.thesis_id,
                WatchlistItem.added_at,
                Thesis.title.label("thesis_title"),
                Thesis.status.label("thesis_status"),
            )
            .outerjoin(Thesis, Thesis.id == WatchlistItem.thesis_id)
            .where(WatchlistItem.user_id == user_id)
            .order_by(WatchlistItem.added_at.desc())
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        return [
            WatchlistSnapshotRow(
                ticker=r.ticker,
                note=r.note,
                thesis_id=r.thesis_id,
                thesis_title=r.thesis_title,
                thesis_status=str(r.thesis_status) if r.thesis_status else None,
                current_price=None,
                added_at=r.added_at,
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _thesis_summary_rows(self, user_id: str) -> list[ThesisSummaryRow]:
        from src.thesis.models import (
            Assumption,
            AssumptionStatus,
            Catalyst,
            CatalystStatus,
            Thesis,
            ThesisReview,
        )

        latest_review_subq = (
            select(
                ThesisReview.thesis_id,
                ThesisReview.verdict,
                ThesisReview.reviewed_at,
                func.row_number()
                .over(
                    partition_by=ThesisReview.thesis_id,
                    order_by=ThesisReview.reviewed_at.desc(),
                )
                .label("rn"),
            )
            .subquery("latest_review")
        )

        total_assumptions_subq = (
            select(
                Assumption.thesis_id,
                func.count(Assumption.id).label("total"),
                func.sum(func.cast(Assumption.status == AssumptionStatus.INVALID, Integer)).label(
                    "invalid"
                ),
            )
            .group_by(Assumption.thesis_id)
            .subquery("assumption_counts")
        )

        total_catalysts_subq = (
            select(
                Catalyst.thesis_id,
                func.count(Catalyst.id).label("total"),
                func.sum(func.cast(Catalyst.status == CatalystStatus.TRIGGERED, Integer)).label(
                    "triggered"
                ),
            )
            .group_by(Catalyst.thesis_id)
            .subquery("catalyst_counts")
        )

        stmt = (
            select(
                Thesis.id,
                Thesis.ticker,
                Thesis.title,
                Thesis.status,
                Thesis.score,
                Thesis.entry_price,
                Thesis.target_price,
                Thesis.stop_loss,
                Thesis.created_at,
                latest_review_subq.c.verdict.label("last_verdict"),
                latest_review_subq.c.reviewed_at.label("last_reviewed_at"),
                func.coalesce(total_assumptions_subq.c.total, 0).label("assumption_count"),
                func.coalesce(total_assumptions_subq.c.invalid, 0).label(
                    "invalid_assumption_count"
                ),
                func.coalesce(total_catalysts_subq.c.total, 0).label("catalyst_count"),
                func.coalesce(total_catalysts_subq.c.triggered, 0).label(
                    "triggered_catalyst_count"
                ),
            )
            .outerjoin(
                latest_review_subq,
                and_(
                    latest_review_subq.c.thesis_id == Thesis.id,
                    latest_review_subq.c.rn == 1,
                ),
            )
            .outerjoin(total_assumptions_subq, total_assumptions_subq.c.thesis_id == Thesis.id)
            .outerjoin(total_catalysts_subq, total_catalysts_subq.c.thesis_id == Thesis.id)
            .where(Thesis.user_id == user_id)
            .order_by(Thesis.created_at.desc())
        )

        result = await self._session.execute(stmt)
        rows = result.all()

        out: list[ThesisSummaryRow] = []
        for r in rows:
            upside_pct: float | None = None
            risk_reward: float | None = None
            if r.entry_price and r.target_price and r.entry_price > 0:
                upside_pct = (r.target_price - r.entry_price) / r.entry_price * 100
            if r.entry_price and r.target_price and r.stop_loss and r.entry_price > r.stop_loss:
                upside = r.target_price - r.entry_price
                downside = r.entry_price - r.stop_loss
                if downside > 0:
                    risk_reward = upside / downside

            tier_label, tier_icon = score_tier(r.score) if r.score is not None else (None, None)

            out.append(
                ThesisSummaryRow(
                    id=r.id,
                    ticker=r.ticker,
                    title=r.title,
                    status=str(r.status.value if hasattr(r.status, "value") else r.status),
                    score=r.score,
                    score_tier=tier_label,
                    score_tier_icon=tier_icon,
                    score_breakdown=None,
                    entry_price=r.entry_price,
                    target_price=r.target_price,
                    stop_loss=r.stop_loss,
                    upside_pct=upside_pct,
                    risk_reward=risk_reward,
                    current_price=None,
                    pnl_pct=None,
                    last_verdict=str(r.last_verdict) if r.last_verdict else None,
                    last_reviewed_at=r.last_reviewed_at,
                    created_at=r.created_at,
                    assumption_count=r.assumption_count or 0,
                    invalid_assumption_count=r.invalid_assumption_count or 0,
                    catalyst_count=r.catalyst_count or 0,
                    triggered_catalyst_count=r.triggered_catalyst_count or 0,
                )
            )
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json_field(value: str | None) -> list | dict | None:
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value  # type: ignore[return-value]
