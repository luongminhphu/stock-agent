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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_db, get_quote_service
from src.platform.db import AsyncSessionLocal
from src.platform.logging import get_logger
from src.portfolio.service import (
    InsufficientQtyError,
    PortfolioService,
    PositionNotFoundError,
)
from src.portfolio.trade_usecase import TradeUseCase

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


class AdjustRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10, description="Mã cổ phiếu, VD: HPG")
    ratio: float = Field(
        ..., gt=0, le=10,
        description=(
            "Tỷ lệ thưởng/tách. VD: 0.15 = cổ tức cổ phiếu 15% (1,000 cp → 1,150 cp); "
            "1.0 = split 1:2 (1,000 cp → 2,000 cp)"
        ),
    )
    reason: str = Field(
        "stock_dividend",
        pattern="^(stock_dividend|split)$",
        description="stock_dividend = thưởng cổ phiếu (ghi kèm DividendRecord); split = chia tách",
    )
    note: str | None = Field(None, max_length=500)


class AdjustResponse(BaseModel):
    trade_id: int
    position_id: int
    ticker: str
    ratio: float
    reason: str
    old_qty: float
    new_qty: float
    old_avg_cost: float
    new_avg_cost: float
    bonus_qty: float = Field(description="Số cp tăng thêm từ sự kiện")


class PositionEditRequest(BaseModel):
    qty: float | None = Field(None, gt=0, description="Số lượng mới — None = giữ nguyên")
    avg_cost: float | None = Field(None, gt=0, description="Giá vốn TB mới — None = giữ nguyên")


class PositionEditResponse(BaseModel):
    position_id: int
    ticker: str
    qty: float
    avg_cost: float


class TradeHistoryItem(BaseModel):
    trade_id: int
    position_id: int
    ticker: str
    trade_type: str = Field(description="buy | sell | adjust | edit")
    qty: float
    price: float
    realized_pnl: float | None
    note: str | None
    traded_at: str


class TradeHistoryResponse(BaseModel):
    ticker: str | None
    count: int
    items: list[TradeHistoryItem]


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

async def _refresh_snapshot_after_commit(
    quote_svc: object,
    user_id: str,
    ticker: str,
    position_closed: bool = False,
) -> None:
    """Refresh snapshot hôm nay sau khi trade/edit ĐÃ commit — isolated session.

    Pattern bắt buộc cho mọi route thay đổi position (buy/sell/adjust/edit):
      1. session.commit() TRƯỚC — positions/trades là source of truth.
      2. Hàm này chạy SAU, trên AsyncSessionLocal riêng, nên:
         - đọc được position vừa commit (read committed),
         - lỗi snapshot (quote hang / DB error) KHÔNG poison transaction của
           trade — trước đây refresh chạy trên shared session trước commit,
           upsert snapshot fail khiến commit trade fail theo → user mất lệnh
           dù lệnh đã đúng.
    Dashboard đọc live positions (Wave 7.1) nên snapshot chỉ là close_price
    fallback; nếu bước này fail, EOD job 15:20 tự sửa.
    Never raises.
    """
    from src.portfolio.eod_snapshot_service import EodSnapshotService

    try:
        async with AsyncSessionLocal() as snap_session:
            eod_svc = EodSnapshotService(session=snap_session, quote_service=quote_svc)
            await eod_svc.refresh_after_trade(
                user_id, ticker, position_closed=position_closed,
            )
            await snap_session.commit()
    except Exception as exc:
        logger.warning(
            "portfolio.snapshot_refresh_after_commit_failed",
            ticker=ticker,
            position_closed=position_closed,
            error=str(exc),
            exc_info=True,
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
        # Commit trade TRƯỚC — trades/positions là source of truth.
        # Snapshot refresh chạy sau, trên session riêng: lỗi snapshot
        # không bao giờ rollback lệnh mua đã commit.
        await session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    await _refresh_snapshot_after_commit(quote_svc, user_id, result.ticker)

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
        # Commit trade TRƯỚC — snapshot refresh chạy sau trên session riêng.
        await session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PositionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InsufficientQtyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    # Full sell → position_closed=True để xoá snapshot hôm nay của ticker.
    await _refresh_snapshot_after_commit(
        quote_svc, user_id, result.ticker,
        position_closed=result.position_closed,
    )

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


# ---------------------------------------------------------------------------
# Stock dividend / split adjustment
# ---------------------------------------------------------------------------

@router.post(
    "/portfolio/adjust",
    response_model=AdjustResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Điều chỉnh vị thế theo cổ tức cổ phiếu / chia tách — qty tăng, giá vốn TB giảm",
)
async def adjust_position(
    body: AdjustRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
    quote_svc: object = Depends(get_quote_service),
) -> AdjustResponse:
    """Áp dụng stock dividend / split lên vị thế đang mở.

    Cost-preserving: qty × (1 + ratio), avg_cost ÷ (1 + ratio) — tổng giá vốn
    không đổi. Cần chạy sau ngày chốt quyền để P&L, sizing và stop-breach
    check không bị lệch khi giá tham chiếu điều chỉnh xuống.

    Audit trail: Trade(ADJUST, price=0) + DividendRecord(STOCK) khi
    reason=stock_dividend.

    Raises 404 khi không có position mở cho ticker.
    Raises 422 khi ratio không hợp lệ.
    """
    svc = PortfolioService(session=session)
    # capture old values cho response — pre-check để 404 sớm trước khi mutate
    existing = await svc._repo.get_open_position(user_id, body.ticker.upper())
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không có vị thế mở cho {body.ticker.upper()}",
        )
    old_qty, old_avg = existing.qty, existing.avg_cost
    try:
        position, trade = await svc.apply_stock_split(
            user_id=user_id,
            ticker=body.ticker,
            ratio=body.ratio,
            reason=body.reason,
            note=body.note,
        )
        # Commit TRƯỚC — snapshot refresh chạy sau trên session riêng.
        await session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except PositionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    # Refresh snapshot SAU commit — isolated session, never-raises.
    await _refresh_snapshot_after_commit(quote_svc, user_id, position.ticker)

    return AdjustResponse(
        trade_id=trade.id,
        position_id=position.id,
        ticker=position.ticker,
        ratio=body.ratio,
        reason=body.reason,
        old_qty=old_qty,
        new_qty=position.qty,
        old_avg_cost=old_avg,
        new_avg_cost=position.avg_cost,
        bonus_qty=position.qty - old_qty,
    )


# ---------------------------------------------------------------------------
# Direct position edit (manual correction)
# ---------------------------------------------------------------------------

@router.put(
    "/portfolio/positions/{ticker}",
    response_model=PositionEditResponse,
    summary="Sửa trực tiếp qty / giá vốn của vị thế — không audit trail",
)
async def edit_position(
    ticker: str,
    body: PositionEditRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
) -> PositionEditResponse:
    """Sửa thẳng qty và/hoặc avg_cost của position đang mở.

    Dùng cho nhập sai / sync với tài khoản thật. Không ghi Trade record,
    không kiểm cost-preserving — người dùng chịu trách nhiệm giá trị nhập.

    Raises 404 khi không có position mở.
    Raises 422 khi không truyền trường nào hoặc giá trị <= 0.
    """
    svc = PortfolioService(session=session)
    try:
        position = await svc.edit_position(
            user_id=user_id, ticker=ticker, qty=body.qty, avg_cost=body.avg_cost,
        )
        # Commit edit TRƯỚC — positions là source of truth. Snapshot refresh
        # chạy sau, trên session riêng: lỗi snapshot không rollback edit.
        await session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except PositionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    # Refresh snapshot hôm nay SAU commit — dashboard phản ánh giá mới ngay.
    # Isolated session + never-raises: edit đã commit an toàn dù bước này lỗi.
    await _refresh_snapshot_after_commit(get_quote_service(), user_id, position.ticker)

    return PositionEditResponse(
        position_id=position.id,
        ticker=position.ticker,
        qty=position.qty,
        avg_cost=position.avg_cost,
    )


# ---------------------------------------------------------------------------
# Trade history
# ---------------------------------------------------------------------------

@router.get(
    "/portfolio/trades",
    response_model=TradeHistoryResponse,
    summary="Lịch sử thay đổi vị thế: BUY / SELL / ADJUST (cổ tức-split)",
)
async def get_trade_history(
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
    ticker: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> TradeHistoryResponse:
    """Trả lịch sử trades mới nhất trước, lọc theo ticker nếu có.

    Bao gồm ADJUST records (cổ tức/split) và EDIT audit records (sửa trực
    tiếp qua PUT /positions/{ticker}) — merge thành một timeline duy nhất,
    sort theo thời gian mới nhất trước.
    """
    svc = PortfolioService(session=session)
    trades = await svc._repo.list_trades(user_id, ticker=ticker, limit=limit)
    edits = await svc._repo.list_position_edits(user_id, ticker=ticker, limit=limit)

    items = [
        TradeHistoryItem(
            trade_id=t.id,
            position_id=t.position_id,
            ticker=t.ticker,
            trade_type=str(t.trade_type).lower(),
            qty=t.qty,
            price=t.price,
            realized_pnl=t.realized_pnl,
            note=t.note,
            traded_at=t.traded_at.isoformat() if t.traded_at else "",
        )
        for t in trades
    ]
    # Merge edit audit records vào cùng timeline — edit có note mô tả old→new
    items += [
        TradeHistoryItem(
            trade_id=e.id,
            position_id=e.position_id,
            ticker=e.ticker,
            trade_type="edit",
            qty=e.new_qty,
            price=e.new_avg_cost,
            realized_pnl=None,
            note=(
                f"Sửa trực tiếp: {e.old_qty:,.0f} → {e.new_qty:,.0f} cp · "
                f"giá vốn {e.old_avg_cost:,.0f} → {e.new_avg_cost:,.0f}"
            ),
            traded_at=e.edited_at.isoformat() if e.edited_at else "",
        )
        for e in edits
    ]
    # Sort lại theo thời gian desc sau khi merge 2 nguồn
    items.sort(key=lambda i: i.traded_at, reverse=True)
    items = items[:limit]

    return TradeHistoryResponse(
        ticker=ticker.upper() if ticker else None,
        count=len(items),
        items=items,
    )


# ---------------------------------------------------------------------------
# Wave 5a — Position sizing preview
# ---------------------------------------------------------------------------

class SizingPreviewResponse(BaseModel):
    """Quantitative sizing for a prospective entry — advisory, not a gate."""

    ticker: str
    entry_price: float
    stop_price: float
    stop_source: str              # "thesis" | "fallback_default"
    equity_vnd: float
    cash_known: bool              # False → cash estimated, size is conservative
    risk_per_trade_pct: float
    risk_budget_vnd: float
    max_qty: int
    max_value_vnd: float
    portfolio_pct_after: float
    cap_reason: str               # "risk" | "concentration" | "cash" | "invalid" | "averaging_down_blocked"
    warnings: list[str]
    pyramiding_note: str = ""     # Wave 8.3 — advisory: adding to a winner without a fresh breakout


@router.get(
    "/portfolio/sizing-preview",
    response_model=SizingPreviewResponse,
    summary="Quantitative position sizing preview for a prospective BUY",
)
async def get_sizing_preview(
    ticker: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
    quote_svc: object = Depends(get_quote_service),
    entry_price: float | None = None,
) -> SizingPreviewResponse:
    """Return max position size from PositionSizingService (Wave 2).

    entry_price: when omitted, uses the live quote (falls back to last-known
    price off-hours). Dashboard calls this when the user opens the quick-trade
    form so the sizing number appears at decision time — the same math the
    Discord /pretrade command shows.
    """
    from src.portfolio.position_sizing_service import PositionSizingService

    ticker = ticker.upper().strip()
    price = entry_price
    if price is None or price <= 0:
        try:
            quote = await quote_svc.get_quote(ticker)  # type: ignore[attr-defined]
            price = float(getattr(quote, "price", 0) or 0)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Không lấy được giá {ticker}: {exc}",
            ) from exc

    svc = PositionSizingService(session)
    result = await svc.size_for_entry(user_id=user_id, ticker=ticker, entry_price=price)
    return SizingPreviewResponse(
        ticker=result.ticker,
        entry_price=result.entry_price,
        stop_price=result.stop_price,
        stop_source=result.stop_source,
        equity_vnd=result.equity_vnd,
        cash_known=result.cash_known,
        risk_per_trade_pct=result.risk_per_trade_pct,
        risk_budget_vnd=result.risk_budget_vnd,
        max_qty=result.max_qty,
        max_value_vnd=result.max_value_vnd,
        portfolio_pct_after=result.portfolio_pct_after,
        cap_reason=result.cap_reason,
        warnings=result.warnings,
        pyramiding_note=result.pyramiding_note,
    )
