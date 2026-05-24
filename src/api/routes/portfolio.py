"""Portfolio trade routes — Buy / Sell quick actions.

Owner: api segment (thin adapter).
No business logic — delegates entirely to PortfolioService.

Route group: /api/v1/portfolio

Endpoints:
    POST /portfolio/buy   — record a BUY trade, update position avg_cost
    POST /portfolio/sell  — record a SELL trade, compute realized P&L

Both endpoints are scoped to the authenticated owner via get_current_user_id.

Error mapping:
    ValueError              → 400 Bad Request
    PositionNotFoundError   → 404 Not Found
    InsufficientQtyError    → 422 Unprocessable Entity
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_db
from src.portfolio.service import (
    InsufficientQtyError,
    PortfolioService,
    PositionNotFoundError,
)

router = APIRouter(tags=["portfolio"])


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class BuyRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10, description="Mã cổ phiếu, VD: VCB")
    qty: float = Field(..., gt=0, description="Số lượng mua (cp)")
    price: float = Field(..., gt=0, description="Giá mua (VND/cp)")
    thesis_id: int | None = Field(None, description="ID thesis liên kết (tuỳ chọn)")
    sector: str | None = Field(None, max_length=64, description="Ngành (tuỳ chọn)")
    note: str | None = Field(None, max_length=500)


class SellRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10, description="Mã cổ phiếu, VD: VCB")
    qty: float = Field(..., gt=0, description="Số lượng bán (cp)")
    price: float = Field(..., gt=0, description="Giá bán (VND/cp)")
    note: str | None = Field(None, max_length=500)


class TradeResponse(BaseModel):
    trade_id: int
    position_id: int
    ticker: str
    trade_type: str
    qty: float
    price: float
    avg_cost: float
    position_qty: float
    realized_pnl: float | None
    position_closed: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/portfolio/buy",
    response_model=TradeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mua cổ phiếu — ghi Trade(BUY) và cập nhật vị thế",
)
async def buy_stock(
    body: BuyRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
) -> TradeResponse:
    """Thực hiện lệnh MUA: tạo Trade(BUY), tính lại avg_cost (VWAP).

    Nếu chưa có position → tạo mới.
    Nếu đã có position → cộng dồn, cập nhật avg_cost.
    """
    svc = PortfolioService(session)
    try:
        position, trade = await svc.buy(
            user_id=user_id,
            ticker=body.ticker.upper().strip(),
            qty=body.qty,
            price=body.price,
            thesis_id=body.thesis_id,
            sector=body.sector,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return TradeResponse(
        trade_id=trade.id,
        position_id=position.id,
        ticker=trade.ticker,
        trade_type="buy",
        qty=trade.qty,
        price=trade.price,
        avg_cost=position.avg_cost,
        position_qty=position.qty,
        realized_pnl=None,
        position_closed=False,
    )


@router.post(
    "/portfolio/sell",
    response_model=TradeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bán cổ phiếu — ghi Trade(SELL) và tính realized P&L",
)
async def sell_stock(
    body: SellRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
) -> TradeResponse:
    """Thực hiện lệnh BÁN: tạo Trade(SELL), tính realized_pnl.

    Partial sell → position vẫn open, qty giảm.
    Full sell → position.closed_at được set.

    Raises 404 khi không có position mở cho ticker.
    Raises 422 khi qty bán > qty đang giữ.
    """
    svc = PortfolioService(session)
    try:
        position, trade = await svc.sell(
            user_id=user_id,
            ticker=body.ticker.upper().strip(),
            qty=body.qty,
            price=body.price,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PositionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InsufficientQtyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return TradeResponse(
        trade_id=trade.id,
        position_id=position.id,
        ticker=trade.ticker,
        trade_type="sell",
        qty=trade.qty,
        price=trade.price,
        avg_cost=position.avg_cost,
        position_qty=position.qty,
        realized_pnl=trade.realized_pnl,
        position_closed=position.closed_at is not None,
    )
