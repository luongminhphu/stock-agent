"""ThesisQueryService — read queries cho thesis list, detail, upcoming catalysts.

Owner: readmodel segment.
Responsibility:
    get_theses_list()                   — list thesis + last review + health score + live price
    get_thesis_detail()                 — full detail + assumption history + score series
    get_upcoming_catalysts()            — catalysts sap toi
    get_thesis_portfolio_aggregate()    — portfolio aggregate: counts + P&L + breakdowns
    get_conviction_timeline()           — conviction score series cho 1 thesis (sparkline)
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Date as SADate
from sqlalchemy import and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.platform.logging import get_logger
from src.thesis.scoring_service import ScoringService, score_tier

logger = get_logger(__name__)

_STALE_DAYS = 14  # thesis chưa review sau N ngày được coi là stale


def _today_utc() -> date:
    return datetime.now(UTC).date()


def _parse_json_field(value: str | None) -> list | dict | None:
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value  # type: ignore[return-value]


def _pnl_status(pnl_pct: float | None) -> str | None:
    """Return a display label for P&L direction used by downstream renderers."""
    if pnl_pct is None:
        return None
    if pnl_pct > 5:
        return "profit"
    if pnl_pct < -3:
        return "loss"
    return "neutral"


def _health_rank(
    confidence: float | None,
    days_since_review: int | None,
) -> str:
    """Derive a health label combining conviction confidence and review staleness.

    Ranks (worst → best):
        no_review   — chưa có review nào
        stale       — đã review nhưng > _STALE_DAYS ngày và confidence thấp
        critical    — confidence < 0.4
        weak        — confidence < 0.6
        neutral     — confidence < 0.75
        strong      — confidence >= 0.75
    """
    if confidence is None:
        return "no_review"
    stale = days_since_review is None or days_since_review > _STALE_DAYS
    if stale and confidence < 0.7:
        return "stale"
    if confidence < 0.4:
        return "critical"
    if confidence < 0.6:
        return "weak"
    if confidence < 0.75:
        return "neutral"
    return "strong"


_scoring_service = ScoringService()


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
            # Eager-load relationships accessed by ScoringService.compute_with_breakdown().
            # Without this, lazy-load inside async context raises MissingGreenlet → 500.
            .options(
                selectinload(Thesis.assumptions),
                selectinload(Thesis.catalysts),
                selectinload(Thesis.reviews),
            )
            .where(*filters)
            .order_by(Thesis.updated_at.desc())
            .limit(limit)
        )

        rows = (await self._session.execute(stmt)).all()
        now = datetime.now(UTC)
        result = []
        for r in rows:
            t = r.Thesis
            tier_label, tier_icon = score_tier(t.score) if t.score is not None else (None, None)

            current_price: float | None = price_map.get(t.ticker)
            pos_data = position_map.get(t.ticker)
            quantity: float | None = pos_data[0] if pos_data else None
            avg_cost: float | None = pos_data[1] if pos_data else None
            effective_entry: float | None = avg_cost if avg_cost else t.entry_price

            pnl_pct: float | None = None
            pnl_abs: float | None = None
            if current_price and effective_entry and effective_entry > 0:
                pnl_pct = round((current_price - effective_entry) / effective_entry * 100, 2)
                if quantity and quantity > 0:
                    pnl_abs = round(quantity * (current_price - effective_entry), 0)

            # --- derived: days_since_review + health_rank ---
            days_since_review: int | None = None
            if r.last_reviewed_at:
                reviewed_dt = (
                    r.last_reviewed_at
                    if r.last_reviewed_at.tzinfo
                    else r.last_reviewed_at.replace(tzinfo=UTC)
                )
                days_since_review = (now - reviewed_dt).days

            # --- upside_pct + risk_reward ---
            upside_pct: float | None = None
            risk_reward: float | None = None
            if t.target_price and effective_entry and effective_entry > 0:
                upside_pct = round(
                    (t.target_price - effective_entry) / effective_entry * 100, 1
                )
                if t.stop_loss and effective_entry > t.stop_loss:
                    downside = effective_entry - t.stop_loss
                    upside = t.target_price - effective_entry
                    if downside > 0:
                        risk_reward = round(upside / downside, 2)

            # --- invalid assumption + triggered catalyst counts (from eager-loaded) ---
            assumptions = t.assumptions or []
            catalysts = t.catalysts or []
            invalid_assumption_count = sum(
                1 for a in assumptions if str(a.status.value) == "invalid"
            )
            triggered_catalyst_count = sum(
                1 for c in catalysts if str(c.status.value) == "triggered"
            )

            # --- score breakdown (4-dimension) — safe: relationships now eager-loaded ---
            try:
                _, score_breakdown = _scoring_service.compute_with_breakdown(t)
            except Exception:
                logger.warning(
                    "thesis_query_service.score_breakdown_failed", thesis_id=t.id
                )
                score_breakdown = None

            result.append(
                {
                    "id": t.id,
                    "ticker": t.ticker,
                    "title": t.title,
                    "status": str(t.status.value),
                    "score": t.score,
                    "score_tier": tier_label,
                    "score_tier_icon": tier_icon,
                    "score_breakdown": score_breakdown,
                    "entry_price": round(effective_entry, 0) if effective_entry else None,
                    "entry_price_source": "avg_cost" if avg_cost else "thesis",
                    "target_price": t.target_price,
                    "stop_loss": t.stop_loss,
                    "upside_pct": upside_pct,
                    "risk_reward": risk_reward,
                    "current_price": current_price,
                    "pnl_pct": pnl_pct,
                    "pnl_abs": pnl_abs,
                    "pnl_status": _pnl_status(pnl_pct),
                    "quantity": quantity,
                    "has_position": bool(quantity and quantity > 0),
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                    "last_verdict": str(r.last_verdict) if r.last_verdict else None,
                    "last_confidence": r.last_confidence,
                    "last_reviewed_at": r.last_reviewed_at.isoformat()
                    if r.last_reviewed_at
                    else None,
                    "days_since_review": days_since_review,
                    "health_rank": _health_rank(r.last_confidence, days_since_review),
                    # counts — use subquery totals for display; breakdown from eager-loaded
                    "n_assumptions": r.n_assumptions,
                    "n_catalysts": r.n_catalysts,
                    # ThesisSummaryRow-compatible fields
                    "assumption_count": r.n_assumptions,
                    "invalid_assumption_count": invalid_assumption_count,
                    "catalyst_count": r.n_catalysts,
                    "triggered_catalyst_count": triggered_catalyst_count,
                    # market data — enriched externally via price_map; None until available
                    "change": None,
                    "change_pct": None,
                    "volume": None,
                    "is_ceiling": None,
                    "is_floor": None,
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
        """Return PENDING catalysts for active theses, ordered by expected_date.

        Includes ALL PENDING catalysts regardless of whether expected_date is set:
        - Catalysts with a date are sorted first (ASC by date).
        - Catalysts without a date (open-ended / condition-based) appear last.

        This intentionally replaces the previous date-range filter which excluded
        every row when expected_date = NULL — making the feature return [] always.

        The ``days`` parameter is kept for API compatibility but is no longer used
        as a hard filter. Callers can use ``days_until`` in the response to apply
        client-side filtering if desired.

        Output dict keys:
            id, thesis_id, description, expected_date (ISO str | None),
            has_date (bool), days_until (int >= 0 | None),
            note, ticker, thesis_ticker (compat), thesis_title, thesis_status.
        """
        from src.thesis.models import Catalyst, CatalystStatus, Thesis, ThesisStatus

        today = _today_utc()

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
                )
                # Dated catalysts sort first (ASC); NULL-date (open-ended) go to the end.
                .order_by(Catalyst.expected_date.asc().nulls_last())
                .limit(100)
            )
        ).all()

        result = []
        for r in rows:
            has_date = r.expected_date is not None

            # Compute days_until; clamp to >= 0 to avoid negative values from
            # catalysts that expired between the last auto-expire job run and now.
            days_until: int | None = None
            if has_date:
                cat_date = (
                    r.expected_date.date()
                    if isinstance(r.expected_date, datetime)
                    else r.expected_date
                )
                days_until = max(0, (cat_date - today).days)

            # thesis_status may arrive as a raw str when selected via .label()
            # instead of loading a full ORM model — guard against AttributeError.
            thesis_status_str = (
                r.thesis_status.value
                if hasattr(r.thesis_status, "value")
                else str(r.thesis_status)
            )

            result.append(
                {
                    "id": r.id,
                    "thesis_id": r.thesis_id,
                    "description": r.description,
                    "expected_date": r.expected_date.isoformat() if r.expected_date else None,
                    # has_date lets UI bucket into "Có ngày" vs "Chờ điều kiện"
                    "has_date": has_date,
                    "days_until": days_until,
                    "note": r.note,
                    # "ticker" is the key read by build_maintenance_embed() in thesis_embeds.py.
                    # "thesis_ticker" kept for backward compatibility with existing API callers.
                    "ticker": r.thesis_ticker,
                    "thesis_ticker": r.thesis_ticker,
                    "thesis_title": r.thesis_title,
                    "thesis_status": thesis_status_str,
                }
            )
        return result

    async def get_thesis_portfolio_aggregate(
        self,
        user_id: str,
        price_map: dict[str, float] | None = None,
        position_map: dict[str, tuple[float, float]] | None = None,
    ) -> dict[str, Any]:
        """Aggregate view toàn bộ thesis active của user.

        Trả về:
            total_theses        — tổng thesis active
            with_position_count — thesis có open position
            reviewed_count      — thesis đã review ít nhất 1 lần
            stale_count         — thesis active chưa review > _STALE_DAYS ngày
            total_cost_basis    — tổng vốn (avg_cost * qty), None nếu thiếu data
            total_market_value  — tổng market value hiện tại, None nếu thiếu data
            total_pnl_abs       — P&L tuyệt đối, None nếu thiếu data
            total_pnl_pct       — P&L % tổng danh mục, None nếu thiếu data
            verdict_breakdown   — {buy, hold, sell, watch, none} — theo last_verdict
            tier_breakdown      — {A, B, C, D, none} — theo score_tier
            pnl_breakdown       — {profit, neutral, loss, none} — theo pnl_status
            generated_at        — ISO timestamp
        """
        from src.thesis.models import Thesis, ThesisReview, ThesisStatus

        price_map = price_map or {}
        position_map = position_map or {}

        # ── Load all active theses + latest review verdict + reviewed_at ───
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
            .join(Thesis, Thesis.id == ThesisReview.thesis_id)
            .where(Thesis.user_id == user_id)
            .subquery()
        )

        rows = (
            await self._session.execute(
                select(
                    Thesis.id,
                    Thesis.ticker,
                    Thesis.entry_price,
                    Thesis.score,
                    latest_review_subq.c.verdict.label("last_verdict"),
                    latest_review_subq.c.reviewed_at.label("last_reviewed_at"),
                )
                .outerjoin(
                    latest_review_subq,
                    and_(
                        latest_review_subq.c.thesis_id == Thesis.id,
                        latest_review_subq.c.rn == 1,
                    ),
                )
                .where(
                    Thesis.user_id == user_id,
                    Thesis.status == ThesisStatus.ACTIVE,
                )
            )
        ).all()

        # ── Aggregate counters ─────────────────────────────────────────────
        total_cost_basis = 0.0
        total_market_value = 0.0
        has_cost = False
        has_market = False

        now = datetime.now(UTC)
        stale_cutoff = now - timedelta(days=_STALE_DAYS)

        with_position_count = 0
        reviewed_count = 0
        stale_count = 0

        verdict_breakdown: dict[str, int] = {"buy": 0, "hold": 0, "sell": 0, "watch": 0, "none": 0}
        tier_breakdown: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0, "none": 0}
        pnl_breakdown: dict[str, int] = {"profit": 0, "neutral": 0, "loss": 0, "none": 0}

        for r in rows:
            current_price: float | None = price_map.get(r.ticker)
            pos_data = position_map.get(r.ticker)
            quantity: float | None = pos_data[0] if pos_data else None
            avg_cost: float | None = pos_data[1] if pos_data else None
            effective_entry: float | None = avg_cost if avg_cost else r.entry_price

            # position count
            if quantity and quantity > 0:
                with_position_count += 1

            # reviewed count + stale count
            if r.last_verdict is not None:
                reviewed_count += 1
                if r.last_reviewed_at:
                    reviewed_dt = (
                        r.last_reviewed_at
                        if r.last_reviewed_at.tzinfo
                        else r.last_reviewed_at.replace(tzinfo=UTC)
                    )
                    if reviewed_dt < stale_cutoff:
                        stale_count += 1
                else:
                    stale_count += 1
            else:
                # never reviewed → also stale
                stale_count += 1

            # P&L calcs
            pnl_pct: float | None = None
            if current_price and effective_entry and effective_entry > 0:
                pnl_pct = (current_price - effective_entry) / effective_entry * 100

            if avg_cost and quantity:
                cb = avg_cost * quantity
                total_cost_basis += cb
                has_cost = True

            if current_price and quantity:
                mv = current_price * quantity
                total_market_value += mv
                has_market = True

            # verdict breakdown
            v_raw = str(r.last_verdict.value) if hasattr(r.last_verdict, "value") else str(r.last_verdict) if r.last_verdict else None
            v_key = v_raw.lower() if v_raw and v_raw.lower() in verdict_breakdown else "none"
            verdict_breakdown[v_key] += 1

            # tier breakdown
            tier_label, _ = score_tier(r.score) if r.score is not None else (None, None)
            t_key = tier_label if tier_label and tier_label in tier_breakdown else "none"
            tier_breakdown[t_key] += 1

            # pnl breakdown
            ps = _pnl_status(pnl_pct)
            p_key = ps if ps and ps in pnl_breakdown else "none"
            pnl_breakdown[p_key] += 1

        total_pnl_abs: float | None = None
        total_pnl_pct: float | None = None
        if has_cost and has_market:
            total_pnl_abs = total_market_value - total_cost_basis
            if total_cost_basis > 0:
                total_pnl_pct = round(total_pnl_abs / total_cost_basis * 100, 2)
            total_pnl_abs = round(total_pnl_abs, 0)

        return {
            "total_theses": len(rows),
            "with_position_count": with_position_count,
            "reviewed_count": reviewed_count,
            "stale_count": stale_count,
            "total_cost_basis": round(total_cost_basis, 0) if has_cost else None,
            "total_market_value": round(total_market_value, 0) if has_market else None,
            "total_pnl_abs": total_pnl_abs,
            "total_pnl_pct": total_pnl_pct,
            "verdict_breakdown": verdict_breakdown,
            "tier_breakdown": tier_breakdown,
            "pnl_breakdown": pnl_breakdown,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    async def get_conviction_timeline(
        self,
        user_id: str,
        thesis_id: int,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Trả về chuỗi conviction score theo thời gian cho 1 thesis.

        Dùng để render sparkline / trend chart trên dashboard.
        Trả về list rỗng nếu thesis không tồn tại hoặc không thuộc user.

        Output mỗi phần tử:
            reviewed_at     — ISO datetime
            confidence      — float 0..1
            verdict         — str (buy/hold/sell/watch/neutral/...)
            reviewed_price  — float | None
        """
        from src.thesis.models import Thesis, ThesisReview

        # Lightweight ownership check — không load full Thesis object
        exists = (
            await self._session.execute(
                select(Thesis.id).where(
                    Thesis.id == thesis_id,
                    Thesis.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if not exists:
            return []

        rows = (
            await self._session.execute(
                select(
                    ThesisReview.reviewed_at,
                    ThesisReview.confidence,
                    ThesisReview.verdict,
                    ThesisReview.reviewed_price,
                )
                .where(ThesisReview.thesis_id == thesis_id)
                .order_by(ThesisReview.reviewed_at.asc())
                .limit(limit)
            )
        ).all()

        return [
            {
                "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                "confidence": r.confidence,
                "verdict": str(r.verdict.value) if hasattr(r.verdict, "value") else str(r.verdict),
                "reviewed_price": r.reviewed_price,
            }
            for r in rows
        ]
