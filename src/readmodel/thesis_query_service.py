"""ThesisQueryService — read queries cho thesis list, detail, upcoming catalysts.

Owner: readmodel segment.
Responsibility:
    get_theses_list()       — list thesis + last review + health score + live price
    get_thesis_detail()     — full detail + assumption history + score series
    get_upcoming_catalysts() — catalysts sap toi
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date as SADate
from sqlalchemy import and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.scoring_service import score_tier

logger = get_logger(__name__)


def _today_utc() -> date:
    return datetime.now(UTC).date()


def _parse_json_field(value: str | None) -> list | dict | None:
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value  # type: ignore[return-value]


class ThesisQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_theses_list(
        self,
        user_id: str,
        status: str | None = "active",
        ticker: str | None = None,
        limit: int = 200,
        price_map: dict[str, float] | None = None,
        position_map: dict[str, tuple[float, float]] | None = None,
    ) -> list[dict[str, Any]]:
        from src.thesis.models import (
            Assumption,
            Catalyst,
            Thesis,
            ThesisReview,
            ThesisStatus,
        )

        price_map = price_map or {}
        position_map = position_map or {}

        filters = [Thesis.user_id == user_id]
        if status and status != "all":
            with contextlib.suppress(ValueError):
                filters.append(Thesis.status == ThesisStatus(status))
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

            current_price: float | None = price_map.get(t.ticker)
            pos_data = position_map.get(t.ticker)
            avg_cost: float | None = pos_data[1] if pos_data else None
            effective_entry: float | None = avg_cost if avg_cost else t.entry_price

            pnl_pct: float | None = None
            if current_price and effective_entry and effective_entry > 0:
                pnl_pct = round((current_price - effective_entry) / effective_entry * 100, 2)

            result.append(
                {
                    "id": t.id,
                    "ticker": t.ticker,
                    "title": t.title,
                    "status": str(t.status.value),
                    "score": t.score,
                    "score_tier": tier_label,
                    "score_tier_icon": tier_icon,
                    "entry_price": round(effective_entry, 0) if effective_entry else None,
                    "entry_price_source": "avg_cost" if avg_cost else "thesis",
                    "target_price": t.target_price,
                    "stop_loss": t.stop_loss,
                    "current_price": current_price,
                    "pnl_pct": pnl_pct,
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

    async def get_thesis_detail(self, user_id: str, thesis_id: int) -> dict[str, Any] | None:
        from src.thesis.models import (
            Assumption,
            Catalyst,
            Thesis,
            ThesisReview,
        )

        thesis = (
            await self._session.execute(select(Thesis).where(Thesis.id == thesis_id))
        ).scalar_one_or_none()
        if thesis is None or thesis.user_id != user_id:
            return None

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
        tier_label, tier_icon = (
            score_tier(thesis.score) if thesis.score is not None else (None, None)
        )

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
                "last_verdict": str(
                    last_review.verdict.value
                    if hasattr(last_review.verdict, "value")
                    else last_review.verdict
                )
                if last_review
                else None,
                "last_confidence": last_review.confidence if last_review else None,
                "n_assumptions": len(assumptions_rows),
                "n_catalysts": len(catalysts_rows),
                "n_reviews": len(reviews_rows),
            },
            "reviews": [_review_dict(r) for r in reviews_rows],
            "assumptions": [_assumption_dict(a) for a in assumptions_rows],
            "catalysts": [_catalyst_dict(c) for c in catalysts_rows],
        }

    async def get_upcoming_catalysts(self, user_id: str, days: int = 30) -> list[dict[str, Any]]:
        from src.thesis.models import Catalyst, CatalystStatus, Thesis, ThesisStatus

        today = _today_utc()
        end_date = today + timedelta(days=days)

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
                    Catalyst.expected_date.isnot(None),
                    cast(Catalyst.expected_date, SADate).between(today, end_date),
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
