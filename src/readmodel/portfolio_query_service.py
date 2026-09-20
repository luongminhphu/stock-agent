"""PortfolioQueryService — thesis-based portfolio view cho dashboard.

Owner: readmodel segment.
Responsibility:
- get_portfolio() — thesis active + open positions + aggregate P&L.
- build_trades_payload() — composition thuan cho trades view (positions x
  snapshot close x realtime price). Chuyen tu api route ve readmodel (Wave A).

Note: day la thesis-centric view (khac voi PnlService la position-centric).
- qty va avg_cost lay tu bang positions (closed_at IS NULL).
- price_map duoc caller inject tu QuoteService.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.scoring_service import score_tier

logger = get_logger(__name__)


def build_trades_payload(
    live_positions: list[Any],
    snap_close: dict[str, tuple[float, str]],
    price_map: dict[str, float],
    market_open: bool,
) -> dict[str, Any]:
    """Pure composition: live positions × snapshot close × realtime prices.

    Khong I/O, khong side-effect — unit-test khong can DB.
    ``live_positions`` items can attrs: ticker, qty, avg_cost, thesis_id.
    ``snap_close``: ticker → (close_price, snapshot_date ISO string).
    Wave 9.3: doc them locked_* (positions cu chua co attrs nay — dung
    getattr fallback 0/None) de dashboard render badge "Khóa bán" va
    quick-trade canh bao khi SELL vuot sellable_qty.
    """
    positions_out = []
    total_cost = 0.0
    total_mkt = 0.0
    for pos in live_positions:
        snap = snap_close.get(pos.ticker)
        if pos.ticker in price_map:
            current_price: float | None = price_map[pos.ticker]
            # Off-hours QuoteService tra last_known (gia cuoi phien) → van stale
            stale = not market_open
        else:
            current_price = snap[0] if snap else None
            stale = True

        cost_basis = pos.avg_cost * pos.qty
        market_value = current_price * pos.qty if current_price is not None else None
        unrealized_pnl = (
            (current_price - pos.avg_cost) * pos.qty if current_price is not None else None
        )
        unrealized_pct = (
            (unrealized_pnl / cost_basis * 100)
            if (unrealized_pnl is not None and cost_basis)
            else None
        )
        total_cost += cost_basis
        total_mkt += market_value if market_value is not None else 0.0
        locked_qty = getattr(pos, "locked_qty", 0.0) or 0.0
        locked_until = getattr(pos, "locked_until", None)
        positions_out.append(
            {
                "ticker": pos.ticker,
                "qty": pos.qty,
                "avg_cost": pos.avg_cost,
                "current_price": current_price,
                "cost_basis": cost_basis,
                "market_value": market_value,
                "unrealized_pnl": round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
                "unrealized_pct": round(unrealized_pct, 4) if unrealized_pct is not None else None,
                "thesis_id": pos.thesis_id,
                "price_stale": stale,
                "snapshot_date": snap[1] if snap else None,
                # Wave 9.3 — locked position surfaces cho dashboard
                "locked_qty": locked_qty,
                "locked_reason": getattr(pos, "locked_reason", None),
                "locked_until": locked_until.isoformat() if locked_until else None,
                "sellable_qty": max(0.0, pos.qty - locked_qty),
            }
        )

    total_pnl = total_mkt - total_cost
    total_pct = (total_pnl / total_cost * 100) if total_cost else 0.0
    return {
        "positions": positions_out,
        "total_unrealized_pnl": round(total_pnl, 2),
        "total_unrealized_pct": round(total_pct, 4),
        "total_cost_basis": total_cost,
        "total_market_value": total_mkt,
        "errors": {},
        "source": "positions_live" + ("+realtime" if market_open and price_map else ""),
    }


class PortfolioQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_portfolio(
        self,
        user_id: str,
        price_map: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        from src.portfolio.models import Position
        from src.thesis.models import Thesis, ThesisReview, ThesisStatus

        price_map = price_map or {}

        pos_rows = (
            await self._session.execute(
                select(
                    Position.ticker,
                    Position.qty,
                    Position.avg_cost,
                    Position.thesis_id,
                ).where(
                    Position.user_id == user_id,
                    Position.closed_at.is_(None),
                    Position.qty > 0,
                )
            )
        ).all()

        pos_map: dict[str, tuple[float, float]] = {}
        for p in pos_rows:
            if p.ticker not in pos_map:
                pos_map[p.ticker] = (p.qty, p.avg_cost)

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

        rows = (
            await self._session.execute(
                select(
                    Thesis.id,
                    Thesis.ticker,
                    Thesis.title,
                    Thesis.status,
                    Thesis.entry_price,
                    Thesis.score,
                    latest_review_subq.c.verdict.label("last_verdict"),
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
                .order_by(Thesis.updated_at.desc())
            )
        ).all()

        positions = []
        total_cost_basis = 0.0
        total_market_value = 0.0
        has_cost_data = False
        has_market_data = False
        has_quantity_data = False
        winning = losing = neutral = 0

        for r in rows:
            current_price = price_map.get(r.ticker)
            tier_label, tier_icon = score_tier(r.score) if r.score is not None else (None, None)

            pos_data = pos_map.get(r.ticker)
            quantity = pos_data[0] if pos_data else None
            avg_cost = pos_data[1] if pos_data else None

            if quantity:
                has_quantity_data = True

            effective_entry: float | None = avg_cost if avg_cost else r.entry_price
            pnl_pct: float | None = None
            if current_price and effective_entry and effective_entry > 0:
                pnl_pct = (current_price - effective_entry) / effective_entry * 100

            cost_basis: float | None = None
            if avg_cost and quantity:
                cost_basis = avg_cost * quantity
                total_cost_basis += cost_basis
                has_cost_data = True

            market_value: float | None = None
            if current_price and quantity:
                market_value = current_price * quantity
                total_market_value += market_value
                has_market_data = True

            pnl_abs: float | None = None
            if cost_basis is not None and market_value is not None:
                pnl_abs = market_value - cost_basis

            if pnl_pct is not None:
                if pnl_pct > 0:
                    winning += 1
                elif pnl_pct < 0:
                    losing += 1
                else:
                    neutral += 1
            else:
                neutral += 1

            positions.append(
                {
                    "thesis_id": r.id,
                    "ticker": r.ticker,
                    "title": r.title,
                    "status": str(r.status.value),
                    "quantity": quantity,
                    "avg_cost": round(avg_cost, 0) if avg_cost else None,
                    "entry_price": r.entry_price,
                    "current_price": current_price,
                    "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
                    "pnl_abs": round(pnl_abs, 0) if pnl_abs is not None else None,
                    "cost_basis": round(cost_basis, 0) if cost_basis is not None else None,
                    "market_value": round(market_value, 0) if market_value is not None else None,
                    "weight_pct": None,
                    "last_verdict": str(r.last_verdict) if r.last_verdict else None,
                    "score": r.score,
                    "score_tier": tier_label,
                    "score_tier_icon": tier_icon,
                    "change_pct": None,
                }
            )

        for pos in positions:
            mv = pos["market_value"]
            if mv is not None and has_market_data and total_market_value > 0:
                pos["weight_pct"] = round(mv / total_market_value * 100, 2)

        positions.sort(key=lambda p: (p["pnl_abs"] is None, -(p["pnl_abs"] or 0), p["ticker"]))

        total_pnl_abs = (
            (total_market_value - total_cost_basis) if (has_cost_data and has_market_data) else None
        )
        total_pnl_pct: float | None = None
        if total_cost_basis > 0 and total_pnl_abs is not None:
            total_pnl_pct = round(total_pnl_abs / total_cost_basis * 100, 2)

        return {
            "user_id": user_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "total_cost_basis": round(total_cost_basis, 0) if has_cost_data else None,
            "total_market_value": round(total_market_value, 0) if has_market_data else None,
            "total_pnl_abs": round(total_pnl_abs, 0) if total_pnl_abs is not None else None,
            "total_pnl_pct": total_pnl_pct,
            "position_count": len(positions),
            "winning_count": winning,
            "losing_count": losing,
            "neutral_count": neutral,
            "has_quantity_data": has_quantity_data,
            "positions": positions,
        }


class PortfolioQueryAdapter:
    """Session-factory–aware adapter for SignalEngineListener.

    PortfolioQueryService nhận một AsyncSession cụ thể tại __init__,
    không phù hợp cho long-running listener (session bị stale sau lần đầu).
    Adapter này tạo session mới cho mỗi lần gọi get_portfolio() để đảm bảo
    connection pool được dùng đúng cách.

    Owner: readmodel segment.
    Used by: platform/bootstrap → SignalEngineListener(portfolio_query=...)
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def get_portfolio(
        self,
        user_id: str,
        price_map: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Open a fresh session, delegate to PortfolioQueryService, close session."""
        async with self._session_factory() as session:
            svc = PortfolioQueryService(session=session)
            return await svc.get_portfolio(user_id=user_id, price_map=price_map)
