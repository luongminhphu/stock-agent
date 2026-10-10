"""Pre-trade check — POST /api/v1/pretrade/check (P1b).

Owner: api segment. Adapter mỏng: nhận lệnh dự kiến từ dashboard (form Giao dịch),
gọi thesis.PreTradeService, trả PreTradeCheckOutput nguyên vẹn. Không có domain logic.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.schemas import PreTradeCheckOutput
from src.api.deps import (
    get_current_user_id,
    get_db,
    get_market_regime_service,
    get_pretrade_agent,
    get_quote_service,
    get_ticker_context_service,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/pretrade", tags=["pretrade"])


class PreTradeCheckRequest(BaseModel):
    """Lệnh nhà đầu tư sắp đặt — AI đánh giá có nên thực hiện đúng lệnh này không."""

    ticker: str = Field(..., min_length=2, max_length=20)
    intended_action: Literal["BUY", "SELL", "REDUCE", "HOLD"] = Field(
        "BUY", description="BUY mở/mua thêm · SELL đóng · REDUCE bán một phần · HOLD giữ"
    )
    quantity: int | None = Field(None, ge=1, le=10_000_000)


@router.post(
    "/check",
    response_model=PreTradeCheckOutput,
    summary="Kiểm tra AI trước lệnh (Mua / Bán / Giảm / Giữ)",
)
async def pretrade_check(
    body: PreTradeCheckRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: Annotated[AsyncSession, Depends(get_db)],
    quote_svc: Annotated[Any, Depends(get_quote_service)],
    agent: Annotated[Any, Depends(get_pretrade_agent)],
    regime_svc: Annotated[Any, Depends(get_market_regime_service)],
    tcs: Annotated[Any, Depends(get_ticker_context_service)],
) -> PreTradeCheckOutput:
    """Cùng luồng với Discord /pretrade; lời khuyên được lưu DecisionLog PRETRADE_ADVICE
    để đối chiếu adherence khi lệnh thật được ghi."""
    from src.thesis.pretrade_service import PreTradeService

    svc = PreTradeService(
        session=session,
        quote_service=quote_svc,
        pretrade_agent=agent,
        market_regime_service=regime_svc,
        ticker_context_service=tcs,
    )
    try:
        result = await svc.check(
            ticker=body.ticker,
            user_id=user_id,
            intended_action=body.intended_action,
            quantity=body.quantity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("pretrade.api.error", ticker=body.ticker, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Không kiểm tra được {body.ticker.upper()}: {exc}",
        ) from exc
    return result
