"""Board route — bảng lõi "Danh mục & Watchlist" (vị thế + tiền lệ từng mã).

Owner: api segment (adapter mỏng). Logic ở readmodel.position_board.

Endpoints:
    GET /board — các mã đang nắm + các mã trong watchlist của người dùng, mỗi mã kèm
                 trạng thái, xác suất tiền lệ 60/120 phiên, lợi thế so với nền, độ tin
                 cậy, gợi ý; mã đang nắm thêm lãi/lỗ, tỉ trọng, mức sụt so với tiền lệ.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_baserate_service, get_current_user_id, get_db, get_quote_service
from src.market.ohlcv_service import _today_ict
from src.readmodel.position_board import PositionBoardService

router = APIRouter(prefix="/board", tags=["board"])


@router.get("")
async def get_board(
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
    quote_svc: Any = Depends(get_quote_service),
    baserate_svc: Any = Depends(get_baserate_service),
) -> dict[str, Any]:
    svc = PositionBoardService(session, quote_svc, baserate_svc)
    return await svc.get_board(user_id, _today_ict())
