"""Portfolio trade routes — Buy / Sell quick actions.

Owner: api segment (thin adapter).
No orchestration logic — delegates entirely to TradeUseCase.

Route group: /api/v1/portfolio

Endpoints:
    POST /portfolio/buy   — record a BUY trade, update position avg_cost
    POST /portfolio/sell  — record a SELL trade, compute realized P&L

Both endpoints are scoped to the authenticated owner via get_current_user_id.

Orchestration (buy/sell + decision log) lives in:
    src/portfolio/trade_usecase.py  ←  single source of truth

This adapter only:
  - Validates the HTTP request via Pydantic DTOs.
  - Calls TradeUseCase.
  - Maps TradeResult → TradeResponse.
  - Maps domain exceptions → HTTP status codes.

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

from src.api.deps import get_current_user_id, get_db, get_quote_service
from src.portfolio.service import (
    InsufficientQtyError,
    PositionNotFoundError,
)
from src.portfolio.trade_usecase import TradeUseCase

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
    rationale: str | None = Field(
        None,
        max_length=500,
        description=(
            "Lý do quyết định mua — nếu cung cấp cùng thesis_id, sẽ tự động tạo DecisionLog. "
            "Nếu để trống nhưng thesis_id có giá trị, backend tự điền rationale mặc định."
        ),
    )


class SellRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10, description="Mã cổ phiếu, VD: VCB")
    qty: float = Field(..., gt=0, description="Số lượng bán (cp)")
    price: float = Field(..., gt=0, description="Giá bán (VND/cp)")
    note: str | None = Field(None, max_length=500)
    thesis_id: int | None = Field(
        None,
        description="ID thesis liên kết — cần thiết để tạo DecisionLog khi bán",
    )
    rationale: str | None = Field(
        None,
        max_length=500,
        description=(
            "Lý do quyết định bán — nếu cung cấp cùng thesis_id, sẽ tự động tạo DecisionLog. "
            "Nếu để trống nhưng thesis_id có giá trị, backend tự điền rationale mặc định."
        ),
    )


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
    decision_logged: bool = Field(
        False,
        description="True nếu DecisionLog đã được tạo thành công cho lệnh này",
    )


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
    quote_svc: object = Depends(get_quote_service),
) -> TradeResponse:
    """Thực hiện lệnh MUA: tạo Trade(BUY), tính lại avg_cost (VWAP).

    Nếu chưa có position → tạo mới.
    Nếu đã có position → cộng dồn, cập nhật avg_cost.

    Nếu thesis_id được cung cấp → tạo DecisionLog(BUY) tự động.
    Rationale do user điền được ưu tiên; nếu không có, backend tự điền mặc định.
    Failure của decision log không ảnh hưởng đến response trade.
    """
    uc = TradeUseCase(session=session, quote_service=quote_svc)
    try:
        result = await uc.execute_buy(
            user_id=user_id,
            ticker=body.ticker,
            qty=body.qty,
            price=body.price,
            thesis_id=body.thesis_id,
            rationale=body.rationale,
            sector=body.sector,
            note=body.note,
            source="dashboard",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return TradeResponse(
        trade_id=result.trade_id,
        position_id=result.position_id,
        ticker=result.ticker,
        trade_type=result.trade_type,
        qty=result.qty,
        price=result.price,
        avg_cost=result.avg_cost,
        position_qty=result.position_qty,
        realized_pnl=result.realized_pnl,
        position_closed=result.position_closed,
        decision_logged=result.decision_logged,
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
    quote_svc: object = Depends(get_quote_service),
) -> TradeResponse:
    """Thực hiện lệnh BÁN: tạo Trade(SELL), tính realized_pnl.

    Partial sell → position vẫn open, qty giảm.
    Full sell → position.closed_at được set.

    Nếu thesis_id được cung cấp → tạo DecisionLog(SELL) tự động.
    Rationale do user điền được ưu tiên; nếu không có, backend tự điền mặc định.
    Failure của decision log không ảnh hưởng đến response trade.

    Raises 404 khi không có position mở cho ticker.
    Raises 422 khi qty bán > qty đang giữ.
    """
    uc = TradeUseCase(session=session, quote_service=quote_svc)
    try:
        result = await uc.execute_sell(
            user_id=user_id,
            ticker=body.ticker,
            qty=body.qty,
            price=body.price,
            thesis_id=body.thesis_id,
            rationale=body.rationale,
            note=body.note,
            source="dashboard",
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
        trade_id=result.trade_id,
        position_id=result.position_id,
        ticker=result.ticker,
        trade_type=result.trade_type,
        qty=result.qty,
        price=result.price,
        avg_cost=result.avg_cost,
        position_qty=result.position_qty,
        realized_pnl=result.realized_pnl,
        position_closed=result.position_closed,
        decision_logged=result.decision_logged,
    )
