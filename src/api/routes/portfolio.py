"""Portfolio trade routes — Buy / Sell quick actions.

Owner: api segment (thin adapter).
No business logic — delegates entirely to PortfolioService.

Route group: /api/v1/portfolio

Endpoints:
    POST /portfolio/buy   — record a BUY trade, update position avg_cost
    POST /portfolio/sell  — record a SELL trade, compute realized P&L

Both endpoints are scoped to the authenticated owner via get_current_user_id.

Decision log (fire-and-forget):
    If thesis_id is provided, a DecisionLog entry is created automatically
    after the trade is persisted.

    - If rationale is also provided → it is used as-is.
    - If rationale is missing → auto-filled as "Quick trade: {type} via dashboard"
      so that every trade linked to a thesis is captured in the decision log
      without requiring the user to type a rationale in the modal.

    Failure to log the decision never blocks the trade response — the trade
    is the source of truth.

    execution_price (the actual fill price) is forwarded to DecisionService
    so that price_at_decision reflects the real trade price, not a live quote.

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
from src.platform.logging import get_logger
from src.portfolio.service import (
    InsufficientQtyError,
    PortfolioService,
    PositionNotFoundError,
)

logger = get_logger(__name__)
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
    # Wave 1: optional decision log fields
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
    # Wave 1: optional decision log fields
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
# Helpers
# ---------------------------------------------------------------------------

_AUTO_RATIONALE_TEMPLATE = "Quick trade: {decision_type} via dashboard"


async def _try_log_decision(
    session: AsyncSession,
    user_id: str,
    ticker: str,
    thesis_id: int | None,
    decision_type: str,
    rationale: str | None,
    execution_price: float | None,
    quote_svc: object,
) -> bool:
    """Fire-and-forget decision log after a trade is persisted.

    Returns True if the DecisionLog was created successfully, False otherwise.
    Failure is always soft — logged as WARNING, never re-raised.

    Contract:
      - Only logs when thesis_id is provided.
      - If rationale is supplied → used as-is (user intent preserved).
      - If rationale is missing → auto-filled as "Quick trade: {type} via dashboard"
        so that every thesis-linked trade generates a DecisionLog automatically.
      - execution_price (actual fill price) is forwarded to DecisionService so
        that price_at_decision reflects the real trade price, not a live quote.
      - Uses the same session; DecisionService commits internally.
    """
    if not thesis_id:
        return False

    # Auto-fill rationale so every thesis-linked trade is captured in decision log.
    # User-provided rationale always takes priority.
    effective_rationale = rationale or _AUTO_RATIONALE_TEMPLATE.format(
        decision_type=decision_type
    )

    if not rationale:
        logger.info(
            "portfolio.decision_log_auto_rationale",
            user_id=user_id,
            ticker=ticker,
            thesis_id=thesis_id,
            decision_type=decision_type,
            auto_rationale=effective_rationale,
        )

    try:
        from src.thesis.decision_service import DecisionService  # noqa: PLC0415

        svc = DecisionService(session=session, quote_service=quote_svc)
        await svc.log_decision(
            user_id=user_id,
            thesis_id=thesis_id,
            decision_type=decision_type,
            rationale=effective_rationale,
            execution_price=execution_price,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "portfolio.decision_log_failed",
            user_id=user_id,
            ticker=ticker,
            thesis_id=thesis_id,
            decision_type=decision_type,
            error=str(exc),
        )
        return False


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
    execution_price = body.price (giá fill thực tế) được forward vào DecisionLog.
    Failure của decision log không ảnh hưởng đến response trade.
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

    decision_logged = await _try_log_decision(
        session=session,
        user_id=user_id,
        ticker=trade.ticker,
        thesis_id=body.thesis_id,
        decision_type="BUY",
        rationale=body.rationale,
        execution_price=body.price,
        quote_svc=quote_svc,
    )

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
        decision_logged=decision_logged,
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
    execution_price = body.price (giá fill thực tế) được forward vào DecisionLog.
    Failure của decision log không ảnh hưởng đến response trade.

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

    decision_logged = await _try_log_decision(
        session=session,
        user_id=user_id,
        ticker=trade.ticker,
        thesis_id=body.thesis_id,
        decision_type="SELL",
        rationale=body.rationale,
        execution_price=body.price,
        quote_svc=quote_svc,
    )

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
        decision_logged=decision_logged,
    )
