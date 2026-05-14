"""StatsService — KPI tong quan cho dashboard.

Owner: readmodel segment.
Responsibility: get_stats() only — open theses, verdict distribution, risky count, catalyst count.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date as SADate
from sqlalchemy import and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger

logger = get_logger(__name__)

_VN_OFFSET = timedelta(hours=7)

# Thesis chưa được review trong N ngày → coi là "stale"
_STALE_REVIEW_DAYS = 14


def _today_utc() -> date:
    return datetime.now(UTC).date()


class StatsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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

        today = _today_utc()
        in_7d = today + timedelta(days=7)
        upcoming_7d = (
            await self._session.scalar(
                select(func.count(Catalyst.id))
                .join(Thesis, Thesis.id == Catalyst.thesis_id)
                .where(
                    Thesis.user_id == user_id,
                    Thesis.status == ThesisStatus.ACTIVE,
                    Catalyst.status == CatalystStatus.PENDING,
                    Catalyst.expected_date.isnot(None),
                    cast(Catalyst.expected_date, SADate).between(today, in_7d),
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

        now_utc = datetime.now(UTC)
        today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        reviews_today = (
            await self._session.scalar(
                select(func.count(ThesisReview.id))
                .join(Thesis, Thesis.id == ThesisReview.thesis_id)
                .where(
                    Thesis.user_id == user_id,
                    ThesisReview.reviewed_at >= today_start_utc,
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

        # Thesis active chưa có review nào trong _STALE_REVIEW_DAYS ngày gần nhất.
        # Dùng để highlight "cần review gấp" trên dashboard.
        stale_cutoff = now_utc - timedelta(days=_STALE_REVIEW_DAYS)

        # Subquery: thesis_id đã có ít nhất 1 review trong window gần đây
        reviewed_recently_subq = (
            select(ThesisReview.thesis_id)
            .where(ThesisReview.reviewed_at >= stale_cutoff)
            .distinct()
            .subquery()
        )

        stale_count = (
            await self._session.scalar(
                select(func.count(Thesis.id)).where(
                    Thesis.user_id == user_id,
                    Thesis.status == ThesisStatus.ACTIVE,
                    Thesis.id.not_in(select(reviewed_recently_subq.c.thesis_id)),
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
            "stale_review_count": stale_count,
            "stale_review_days": _STALE_REVIEW_DAYS,
        }
