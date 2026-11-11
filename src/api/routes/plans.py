"""Plans routes — "Kế hoạch nắm giữ" (cách đọc thesis cho người không làm tài chính).

Owner: api segment (adapter mỏng). Logic ở thesis.plan / thesis.plan_service.

Endpoints:
    GET  /plans/templates  — mẫu "điều tôi tin" hệ thống tự kiểm được bằng số liệu
    GET  /plans/suggest    — gợi ý cho 1 mã: điều nào đang đúng, mức chấp nhận sai / chốt lời
    GET  /plans            — kế hoạch đang mở: đèn, lý do, tóm tắt, điều tôi tin, sự kiện
    POST /plans            — tạo kế hoạch (4 câu hỏi)
    POST /plans/check      — kiểm tra ngay (ghi kết quả; bình thường chạy tự động sau đóng cửa)
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_baserate_service, get_current_user_id, get_db, get_quote_service
from src.market.baserate import FEATURE_LABELS
from src.market.ohlcv_service import _today_ict
from src.market.price_rules import snap_to_tick
from src.market.registry import registry
from src.thesis.plan import BELIEF_TEMPLATES, check_belief, suggest_levels
from src.thesis.plan_service import NewBelief, NewEvent, PlanCheckService, create_plan

router = APIRouter(prefix="/plans", tags=["plans"])


class BeliefIn(BaseModel):
    text: str = Field(default="", max_length=500)
    check_key: str | None = Field(default=None, max_length=32)


class EventIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    expected_date: date | None = None


class PlanCreate(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    title: str = Field(default="", max_length=255)
    entry_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    review_by: date | None = None
    beliefs: list[BeliefIn] = Field(default_factory=list, max_length=10)
    events: list[EventIn] = Field(default_factory=list, max_length=10)
    note: str | None = Field(default=None, max_length=2000)


def _state_provider(baserate_svc: Any) -> Any:
    async def provider(ticker: str) -> dict[str, str] | None:
        rep = await baserate_svc.analyze(ticker)
        return dict(rep.state) or None

    return provider


def _serialize(thesis: Any, ev: Any, price: float | None) -> dict[str, Any]:
    return {
        "id": thesis.id,
        "ticker": thesis.ticker,
        "title": thesis.title,
        "status": thesis.status.value if hasattr(thesis.status, "value") else thesis.status,
        "light": ev.light,
        "reasons": ev.reasons,
        "summary": ev.summary,
        "price": price,
        "entry_price": thesis.actual_entry_price or thesis.entry_price,
        "stop_price": thesis.stop_loss,
        "target_price": thesis.target_price,
        "review_by": thesis.review_by,
        "days_left": ev.days_left,
        "pnl_pct": ev.pnl_pct,
        "to_stop_pct": ev.to_stop_pct,
        "to_target_pct": ev.to_target_pct,
        "beliefs": [
            {"text": b.text, "check_key": b.check_key, "result": b.result} for b in ev.beliefs
        ],
        "events": [
            {
                "text": c.description,
                "expected_date": c.expected_date.date() if c.expected_date else None,
                "status": c.status.value if hasattr(c.status, "value") else c.status,
            }
            for c in getattr(thesis, "catalysts", []) or []
        ],
        "checked_at": thesis.plan_checked_at,
    }


@router.get("/templates")
async def get_templates() -> list[dict[str, str]]:
    return [
        {"key": t.key, "label": t.label, "explain": t.explain} for t in BELIEF_TEMPLATES.values()
    ]


@router.get("/suggest")
async def suggest(
    ticker: str = Query(..., min_length=1, max_length=10),
    entry: float | None = Query(default=None, gt=0),
    quote_svc: Any = Depends(get_quote_service),
    baserate_svc: Any = Depends(get_baserate_service),
) -> dict[str, Any]:
    """Gợi ý khi tạo kế hoạch: điều nào hiện đang đúng với mã + mức gợi ý từ tiền lệ."""
    t = ticker.upper()
    if registry.get(t) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không có mã '{t}'.")
    if entry is None:
        try:
            entry = (await quote_svc.get_quote(t)).settled_price
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Không lấy được giá {t}: {exc}"
            ) from exc
    try:
        rep = await baserate_svc.analyze(t)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Không lấy được tiền lệ {t}: {exc}"
        ) from exc
    o = rep.outcomes.get(120)
    exchange = getattr(registry.get(t).exchange, "value", "")  # type: ignore[union-attr]
    lv = suggest_levels(
        float(entry),
        _today_ict(),
        typical_drawdown=o.avg_max_drawdown if o else None,
        median_return=o.median_return if o else None,
        p90_return=o.p90_return if o else None,
        round_to=lambda x: snap_to_tick(x, exchange),
    )
    return {
        "ticker": t,
        "entry_price": entry,
        "stop_price": lv.stop_price,
        "stop_note": lv.stop_note,
        "target_price": lv.target_price,
        "target_note": lv.target_note,
        "review_by": lv.review_by,
        "episodes": o.episodes if o else 0,
        "state_labels": {k: FEATURE_LABELS.get(k, {}).get(v, v) for k, v in rep.state.items()},
        "templates": [
            {
                "key": tpl.key,
                "label": tpl.label,
                "explain": tpl.explain,
                "now": check_belief(tpl.key, rep.state),
            }
            for tpl in BELIEF_TEMPLATES.values()
        ],
    }


@router.get("")
async def list_plans(
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
    quote_svc: Any = Depends(get_quote_service),
    baserate_svc: Any = Depends(get_baserate_service),
) -> list[dict[str, Any]]:
    svc = PlanCheckService(session, quote_svc, _state_provider(baserate_svc))
    evaluated = await svc.evaluate_open(_today_ict(), user_id)
    order = {"do": 0, "vang": 1, "xanh": 2, "xam": 3}
    rows = [_serialize(p, ev, px) for p, ev, px in evaluated]
    rows.sort(key=lambda r: (order.get(r["light"], 9), r["ticker"]))
    return rows


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: PlanCreate,
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    t = body.ticker.upper().strip()
    if registry.get(t) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không có mã '{t}'.")
    if body.stop_price and body.entry_price and body.stop_price >= body.entry_price:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Mức chấp nhận sai phải thấp hơn giá mua.",
        )
    plan = await create_plan(
        session,
        user_id=user_id,
        ticker=t,
        title=body.title or f"Kế hoạch nắm giữ {t}",
        entry_price=body.entry_price,
        stop_price=body.stop_price,
        target_price=body.target_price,
        review_by=body.review_by,
        beliefs=[NewBelief(b.text, b.check_key) for b in body.beliefs],
        events=[NewEvent(e.text, e.expected_date) for e in body.events],
        note=body.note,
        today=_today_ict(),
    )
    await session.commit()
    return {"id": plan.id, "ticker": plan.ticker, "review_by": plan.review_by}


@router.post("/check")
async def check_now(
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_db),
    quote_svc: Any = Depends(get_quote_service),
    baserate_svc: Any = Depends(get_baserate_service),
) -> dict[str, Any]:
    svc = PlanCheckService(session, quote_svc, _state_provider(baserate_svc))
    changes = await svc.run(_today_ict(), user_id)
    await session.commit()
    return {
        "changed": [
            {"id": c.thesis_id, "ticker": c.ticker, "from": c.old_light, "to": c.new_light}
            for c in changes
        ]
    }
