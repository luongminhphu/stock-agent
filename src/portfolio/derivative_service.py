"""DerivativePositionService — sổ vị thế HĐTL VN30 (mở / đóng / xem lãi lỗ).

Owner: portfolio segment. Công thức lãi lỗ, ký quỹ, ngày đáo hạn lấy từ market.derivatives.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select

from src.market.derivatives import LONG, SHORT, pnl, position_view
from src.portfolio.models import DerivativePosition


class DerivativePositionError(ValueError):
    pass


class DerivativePositionService:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def open(
        self,
        user_id: str,
        contract_code: str,
        side: str,
        qty: int,
        entry_price: float,
        purpose: str | None = None,
        note: str | None = None,
    ) -> DerivativePosition:
        side = side.upper()
        if side not in (LONG, SHORT):
            raise DerivativePositionError("side phải là LONG hoặc SHORT")
        if qty <= 0 or entry_price <= 0:
            raise DerivativePositionError("Số hợp đồng và giá vào phải > 0")
        pos = DerivativePosition(
            user_id=user_id,
            contract_code=contract_code.upper().strip(),
            side=side,
            qty=qty,
            entry_price=entry_price,
            purpose=purpose,
            note=note,
        )
        self._session.add(pos)
        await self._session.flush()
        return pos

    async def close(self, user_id: str, position_id: int, exit_price: float) -> DerivativePosition:
        pos = await self._session.get(DerivativePosition, position_id)
        if pos is None or pos.user_id != user_id:
            raise DerivativePositionError("Không tìm thấy vị thế")
        if pos.closed_at is not None:
            raise DerivativePositionError("Vị thế đã đóng")
        if exit_price <= 0:
            raise DerivativePositionError("Giá đóng phải > 0")
        pos.exit_price = exit_price
        pos.closed_at = datetime.now(UTC)
        pos.realized_pnl = round(pnl(pos.side, pos.qty, pos.entry_price, exit_price))
        await self._session.flush()
        return pos

    async def list(self, user_id: str, include_closed: bool = False) -> list[DerivativePosition]:
        stmt = select(DerivativePosition).where(DerivativePosition.user_id == user_id)
        if not include_closed:
            stmt = stmt.where(DerivativePosition.closed_at.is_(None))
        stmt = stmt.order_by(DerivativePosition.opened_at.desc())
        return list((await self._session.execute(stmt)).scalars().all())

    @staticmethod
    def view(pos: DerivativePosition, mark_price: float | None, today: date) -> dict[str, Any]:
        """Vị thế + lãi/lỗ theo giá hiện tại (hoặc lãi/lỗ đã chốt nếu đã đóng)."""
        base = {
            "id": pos.id,
            "contract_code": pos.contract_code,
            "side": pos.side,
            "qty": pos.qty,
            "entry_price": pos.entry_price,
            "opened_at": pos.opened_at,
            "purpose": pos.purpose,
            "note": pos.note,
            "closed_at": pos.closed_at,
            "exit_price": pos.exit_price,
            "realized_pnl": pos.realized_pnl,
        }
        price = pos.exit_price if pos.closed_at else mark_price
        if price:
            v = position_view(pos.contract_code, pos.side, pos.qty, pos.entry_price, price, today)
            base.update(
                mark_price=price,
                unrealized_pnl=None if pos.closed_at else v.unrealized_pnl,
                pnl_points=v.pnl_points,
                initial_margin=v.initial_margin,
                pnl_on_margin_pct=v.pnl_on_margin_pct,
                expiry=v.expiry,
                days_to_expiry=v.days_to_expiry,
                roll_warning=v.roll_warning and not pos.closed_at,
            )
        else:
            base.update(mark_price=None, unrealized_pnl=None)
        return base
