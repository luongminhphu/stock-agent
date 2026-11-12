"""Derivatives routes — HĐTL VN30: kỳ hạn & giá, basis, tiền lệ VN30, kế hoạch phòng hộ danh mục.

Owner: api segment (adapter mỏng). Logic nằm ở market.derivatives.

Endpoints:
    GET /derivatives/contracts       — 4 HĐ đang lưu hành, ngày đáo hạn, giá, basis
    GET /derivatives/basis           — thống kê basis lịch sử theo số ngày tới đáo hạn
    GET /derivatives/vn30/baserate   — tiền lệ của VN30 ở trạng thái hiện tại (5/20/60 phiên)
    GET /derivatives/hedge           — số HĐ cần Short để phòng hộ danh mục cổ phiếu đang mở
    GET /derivatives/position-preview — lãi/lỗ, ký quỹ, cảnh báo đáo hạn cho 1 vị thế giả định
    GET/POST /derivatives/positions, POST /derivatives/positions/{id}/close — sổ vị thế
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_db, get_derivatives_service, get_quote_service
from src.api.dto.market import BaseRateResponse
from src.api.routes.market import baserate_response
from src.market.derivatives import IM_RATE_DEFAULT, hedge_plan, position_view
from src.market.ohlcv_service import _today_ict

router = APIRouter(prefix="/derivatives", tags=["derivatives"])


class ContractRow(BaseModel):
    series: str
    code: str
    krx: str
    expiry: date
    days_to_expiry: int
    price: float | None
    basis: float | None
    contract_value: float | None


class ContractsResponse(BaseModel):
    as_of: date
    vn30: float | None
    contracts: list[ContractRow]


class BasisResponse(BaseModel):
    current: float | None
    samples: int
    median: float | None
    p10: float | None
    p90: float | None
    percentile_of_current: float | None
    by_days_to_expiry: dict[str, float]


class HedgeHolding(BaseModel):
    ticker: str
    market_value: float
    weight: float
    beta: float | None


class HedgeResponse(BaseModel):
    portfolio_value: float
    portfolio_beta: float
    beta_exposure: float
    hedge_ratio: float
    contract_code: str
    futures_price: float
    price_is_index_proxy: bool  # True: chưa có giá HĐ, tạm dùng mức VN30
    contracts_to_short: int
    hedged_value: float
    margin_required: float
    basis_points: float | None
    basis_cost: float | None
    holdings: list[HedgeHolding]
    missing_beta: list[str]


class PositionPreviewResponse(BaseModel):
    code: str
    side: str
    qty: int
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    pnl_points: float
    notional: float
    initial_margin: float
    pnl_on_margin_pct: float
    expiry: date | None
    days_to_expiry: int | None
    roll_warning: bool


def _bad_gateway(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Derivatives data unavailable: {exc}"
    )


@router.get("/contracts", response_model=ContractsResponse)
async def get_contracts(svc: Any = Depends(get_derivatives_service)) -> ContractsResponse:
    try:
        snap = await svc.contracts(_today_ict())
    except Exception as exc:
        raise _bad_gateway(exc) from exc
    return ContractsResponse(**snap)


@router.get("/basis", response_model=BasisResponse)
async def get_basis(
    days: int = Query(default=730, ge=60, le=3650),
    svc: Any = Depends(get_derivatives_service),
) -> BasisResponse:
    try:
        st = await svc.basis_history(_today_ict(), days=days)
    except Exception as exc:
        raise _bad_gateway(exc) from exc
    return BasisResponse(**vars(st))


@router.get("/vn30/baserate", response_model=BaseRateResponse)
async def get_vn30_baserate(svc: Any = Depends(get_derivatives_service)) -> BaseRateResponse:
    try:
        rep = await svc.vn30_baserate()
    except Exception as exc:
        raise _bad_gateway(exc) from exc
    return baserate_response(rep)


@router.get("/hedge", response_model=HedgeResponse)
async def get_hedge(
    user_id: Annotated[str, Depends(get_current_user_id)],
    ratio: float = Query(default=1.0, gt=0, le=1.5, description="Tỉ lệ phòng hộ (1.0 = toàn bộ)"),
    series: str = Query(default="F1M", pattern="^(F1M|F2M|F1Q|F2Q)$"),
    session: AsyncSession = Depends(get_db),
    quote_svc: Any = Depends(get_quote_service),
    svc: Any = Depends(get_derivatives_service),
) -> HedgeResponse:
    """Phòng hộ danh mục cổ phiếu đang mở bằng Short HĐTL VN30 theo beta 120 phiên."""
    from src.portfolio.repository import PortfolioRepository

    positions = await PortfolioRepository(session).list_open_positions(user_id)
    if not positions:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chưa có vị thế mở.")
    tickers = [p.ticker.upper() for p in positions]
    try:
        quotes = {q.ticker.upper(): q for q in await quote_svc.get_bulk_quotes(tickers)}
    except Exception:  # noqa: BLE001 — thiếu giá → dùng giá vốn
        quotes = {}
    try:
        betas = await svc.betas(tickers)
        snap = await svc.contracts(_today_ict())
    except Exception as exc:
        raise _bad_gateway(exc) from exc

    holdings = []
    for p in positions:
        t = p.ticker.upper()
        px = quotes[t].settled_price if t in quotes else p.avg_cost
        holdings.append((t, float(p.qty) * px, betas.get(t)))
    row = next(c for c in snap["contracts"] if c["series"] == series)
    proxy = row["price"] is None
    fut_px = row["price"] if not proxy else snap["vn30"]
    if not fut_px:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chưa có giá HĐ tương lai lẫn mức VN30.",
        )
    plan = hedge_plan(
        holdings,
        fut_px,
        row["code"],
        hedge_ratio=ratio,
        index_level=snap["vn30"],
        im_rate=IM_RATE_DEFAULT,
    )
    data = vars(plan) | {
        "holdings": [HedgeHolding(**vars(h)) for h in plan.holdings],
        "price_is_index_proxy": proxy,
    }
    return HedgeResponse(**data)


@router.get("/position-preview", response_model=PositionPreviewResponse)
async def preview_position(
    code: str = Query(..., min_length=7, max_length=12),
    side: str = Query(..., pattern="^(LONG|SHORT|long|short)$"),
    qty: int = Query(..., ge=1, le=10_000),
    entry: float = Query(..., gt=0),
    mark: float = Query(..., gt=0),
) -> PositionPreviewResponse:
    """Lãi/lỗ theo hệ số 100.000đ/điểm, ký quỹ ban đầu, số ngày tới đáo hạn của một vị thế."""
    v = position_view(code, side, qty, entry, mark, _today_ict())
    return PositionPreviewResponse(**vars(v))


# ── Sổ vị thế phái sinh ──────────────────────────────────────────────────────


class OpenPositionRequest(BaseModel):
    contract_code: str = Field(..., min_length=7, max_length=16)
    side: str = Field(..., pattern="^(LONG|SHORT|long|short)$")
    qty: int = Field(..., ge=1, le=10_000)
    entry_price: float = Field(..., gt=0)
    purpose: str | None = Field(default=None, pattern="^(hedge|trade)$")
    note: str | None = Field(default=None, max_length=1000)


class ClosePositionRequest(BaseModel):
    exit_price: float = Field(..., gt=0)


async def _mark_prices(svc: Any) -> dict[str, float]:
    try:
        snap = await svc.contracts(_today_ict())
    except Exception:  # noqa: BLE001 — không có giá thì vẫn trả vị thế
        return {}
    out: dict[str, float] = {}
    for c in snap.get("contracts", []):
        if c.get("price"):
            out[c["code"]] = c["price"]
            out[c["krx"]] = c["price"]
    return out


@router.get("/positions")
async def list_positions(
    user_id: Annotated[str, Depends(get_current_user_id)],
    include_closed: bool = Query(default=False),
    session: AsyncSession = Depends(get_db),
    svc: Any = Depends(get_derivatives_service),
) -> dict[str, Any]:
    """Vị thế phái sinh + lãi/lỗ theo giá hợp đồng hiện tại; tổng lãi/lỗ và ký quỹ đang dùng."""
    from src.portfolio.derivative_service import DerivativePositionService

    dps = DerivativePositionService(session)
    positions = await dps.list(user_id, include_closed=include_closed)
    marks = await _mark_prices(svc) if any(p.closed_at is None for p in positions) else {}
    today = _today_ict()
    rows = [dps.view(p, marks.get(p.contract_code), today) for p in positions]
    open_rows = [r for r in rows if r["closed_at"] is None]
    return {
        "positions": rows,
        "open_unrealized_pnl": sum(r.get("unrealized_pnl") or 0 for r in open_rows),
        "open_margin": sum(r.get("initial_margin") or 0 for r in open_rows),
        "realized_pnl": sum(r.get("realized_pnl") or 0 for r in rows if r["closed_at"]),
    }


@router.post("/positions", status_code=status.HTTP_201_CREATED)
async def open_position(
    body: OpenPositionRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from src.portfolio.derivative_service import (
        DerivativePositionError,
        DerivativePositionService,
    )

    try:
        pos = await DerivativePositionService(session).open(
            user_id,
            body.contract_code,
            body.side,
            body.qty,
            body.entry_price,
            body.purpose,
            body.note,
        )
    except DerivativePositionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await session.commit()
    return {"id": pos.id}


@router.post("/positions/{position_id}/close")
async def close_position(
    position_id: int,
    body: ClosePositionRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from src.portfolio.derivative_service import (
        DerivativePositionError,
        DerivativePositionService,
    )

    try:
        pos = await DerivativePositionService(session).close(user_id, position_id, body.exit_price)
    except DerivativePositionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await session.commit()
    return {"id": pos.id, "realized_pnl": pos.realized_pnl}


# ── Giả lập tự động (paper trading theo base rate) ──────────────────────────


class SimConfigRequest(BaseModel):
    """Chỉ trường được gửi mới thay đổi. Gửi null cho stop/target/alert/ATR/crowd để tắt."""

    active: bool | None = None
    series: str | None = Field(default=None, pattern="^(F1M|F2M|F1Q|F2Q)$")
    qty: int | None = Field(default=None, ge=1, le=1000)
    mode: str | None = Field(default=None, pattern="^(follow|fade|auto)$")
    stop_points: float | None = Field(default=None, ge=0, le=500)
    target_points: float | None = Field(default=None, ge=0, le=500)
    alert_margin_pct: float | None = Field(default=None, ge=0, le=500)
    stop_atr: float | None = Field(default=None, ge=0, le=10)
    target_atr: float | None = Field(default=None, ge=0, le=10)
    crowd_atr: float | None = Field(default=None, ge=0, le=10)
    avoid_round: bool | None = None


def _sim_service(session: AsyncSession, svc: Any) -> Any:
    from src.portfolio.deriv_sim_service import DerivSimService

    return DerivSimService(session, svc)


@router.get("/sim")
async def get_sim(
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
    svc: Any = Depends(get_derivatives_service),
) -> dict[str, Any]:
    """Trạng thái giả lập: cấu hình, vị thế đang mở, lệnh đã đóng, win rate, đường P&L — hai sổ."""
    return await _sim_service(session, svc).view(user_id, _today_ict())


@router.put("/sim")
async def update_sim(
    body: SimConfigRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
    svc: Any = Depends(get_derivatives_service),
) -> dict[str, Any]:
    from src.portfolio.deriv_sim_service import DerivSimError

    sent = body.model_fields_set
    kw: dict[str, Any] = {
        k: getattr(body, k)
        for k in ("active", "series", "qty", "mode", "avoid_round")
        if k in sent and getattr(body, k) is not None
    }
    for k, clear in (
        ("stop_points", "clear_stop"),
        ("target_points", "clear_target"),
        ("alert_margin_pct", "clear_alert"),
        ("stop_atr", "clear_stop_atr"),
        ("target_atr", "clear_target_atr"),
        ("crowd_atr", "clear_crowd"),
    ):
        if k in sent:
            v = getattr(body, k)
            if v:
                kw[k] = v
            else:
                kw[clear] = True
    sim = _sim_service(session, svc)
    try:
        await sim.update_config(user_id, **kw)
    except DerivSimError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await session.commit()
    return await sim.view(user_id, _today_ict())


@router.post("/sim/reset")
async def reset_sim(
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
    svc: Any = Depends(get_derivatives_service),
) -> dict[str, Any]:
    """Xoá toàn bộ lệnh + đường P&L giả lập và tắt giả lập (giữ cấu hình)."""
    sim = _sim_service(session, svc)
    await sim.reset(user_id)
    await session.commit()
    return await sim.view(user_id, _today_ict())
