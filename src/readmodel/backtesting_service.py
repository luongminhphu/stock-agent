"""BacktestingService — backtesting queries: verdict accuracy, thesis performance, price snapshots.

Owner: readmodel segment.
Responsibility:
    get_verdict_accuracy()    — accuracy per verdict type
    get_thesis_performances() — performance per thesis
    get_price_snapshots()     — price + pnl chart data for one thesis
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger

logger = get_logger(__name__)

# NEUTRAL is considered "correct" when price stays within this band (absolute %)
NEUTRAL_ACCURACY_THRESHOLD = 5.0


class BacktestingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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

        thesis_verdict: dict[int, str] = {r.thesis_id: str(r.verdict) for r in review_rows}

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
            elif verdict == "NEUTRAL" and abs(pnl) < NEUTRAL_ACCURACY_THRESHOLD:
                bucket["hits"] += 1

        result = []
        for verdict in sorted(stats.keys()):
            b = stats[verdict]
            total = b["total"]
            avg_pnl = round(b["pnl_sum"] / total, 2) if total else None
            accuracy_pct = round(b["hits"] / total * 100, 2) if total else None
            result.append(
                {
                    "verdict": verdict,
                    "total": total,
                    "avg_pnl": avg_pnl,
                    "accuracy_pct": accuracy_pct,
                }
            )

        return result

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
                ).where(
                    ThesisSnapshot.thesis_id.in_(thesis_ids),
                    ThesisSnapshot.pnl_pct.isnot(None),
                )
            )
        ).all()

        agg: dict[int, dict] = defaultdict(lambda: {"pnl_values": [], "last_snapshot_at": None})

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

    async def get_price_snapshots(self, user_id: str, thesis_id: int) -> dict[str, Any] | None:
        from src.thesis.models import Thesis, ThesisReview, ThesisSnapshot

        thesis = (
            await self._session.execute(select(Thesis).where(Thesis.id == thesis_id))
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
