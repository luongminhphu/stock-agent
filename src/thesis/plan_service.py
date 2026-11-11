"""PlanCheckService — kiểm tra "Kế hoạch nắm giữ" mỗi ngày sau đóng cửa.

Owner: thesis segment. Không gọi AI.

Thay cho 5 bộ theo dõi chồng chéo (drift giá, drift niềm tin, watchdog AI, thủng
stop-loss, AI xác nhận vô hiệu): MỘT lượt kiểm tra sau khi giá đóng cửa đã chốt.
    1. Lấy các kế hoạch đang mở (ACTIVE / WEAKENING / PAUSED) kèm "điều tôi tin".
    2. Giá: giá đóng cửa đã đối soát (Quote.settled_price) — lấy theo lô.
    3. Trạng thái số liệu của mã (state của market.baserate) — để tự kiểm điều tôi tin.
    4. thesis.plan.evaluate_plan → đèn + lý do + tóm tắt; ghi vào theses.plan_*.
       Điều tôi tin có check_key được cập nhật status (VALID / INVALID / UNCERTAIN).
    5. Trả về danh sách kế hoạch đổi đèn → caller gửi MỘT bản tổng hợp.

Không tự đóng / vô hiệu kế hoạch — quyết định thuộc về người dùng.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.platform.logging import get_logger
from src.thesis.models import AssumptionStatus, Thesis, ThesisStatus
from src.thesis.plan import (
    BELIEF_FALSE,
    BELIEF_TRUE,
    BELIEF_UNKNOWN,
    BeliefInput,
    PlanEvaluation,
    evaluate_plan,
)

logger = get_logger(__name__)

OPEN_STATUSES = (ThesisStatus.ACTIVE, ThesisStatus.WEAKENING, ThesisStatus.PAUSED)
StateProvider = Callable[[str], Awaitable[Mapping[str, str] | None]]
_MAX_CONCURRENT_STATES = 3


@dataclass
class PlanChange:
    thesis_id: int
    user_id: str
    ticker: str
    title: str
    old_light: str | None
    new_light: str
    summary: str
    reasons: list[str]


def evaluate_thesis(
    thesis: Thesis,
    price: float | None,
    today: date,
    state: Mapping[str, str] | None,
) -> PlanEvaluation:
    beliefs = [
        BeliefInput(
            a.description,
            getattr(a, "check_key", None),
            a.status.value if hasattr(a.status, "value") else str(a.status),
        )
        for a in thesis.assumptions
    ]
    return evaluate_plan(
        closed=thesis.status not in OPEN_STATUSES,
        entry_price=thesis.actual_entry_price or thesis.entry_price,
        stop_price=thesis.stop_loss,
        target_price=thesis.target_price,
        review_by=thesis.review_by,
        beliefs=beliefs,
        price=price,
        today=today,
        state=state,
    )


_STATUS_FROM_RESULT = {
    BELIEF_TRUE: AssumptionStatus.VALID,
    BELIEF_FALSE: AssumptionStatus.INVALID,
    BELIEF_UNKNOWN: AssumptionStatus.UNCERTAIN,
}


class PlanCheckService:
    def __init__(
        self,
        session: Any,
        quote_service: Any = None,
        state_provider: StateProvider | None = None,
    ) -> None:
        self._session = session
        self._quotes = quote_service
        self._state = state_provider
        self._sem = asyncio.Semaphore(_MAX_CONCURRENT_STATES)

    async def _open_plans(self, user_id: str | None) -> list[Thesis]:
        stmt = (
            select(Thesis)
            .where(Thesis.status.in_(OPEN_STATUSES))
            .options(selectinload(Thesis.assumptions))
            .order_by(Thesis.id)
        )
        if user_id is not None:
            stmt = stmt.where(Thesis.user_id == user_id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def _prices(self, tickers: list[str]) -> dict[str, float]:
        if not tickers or self._quotes is None:
            return {}
        try:
            quotes = await self._quotes.get_bulk_quotes(tickers)
        except Exception as exc:  # noqa: BLE001 — thiếu giá vẫn kiểm được điều tôi tin/hạn
            logger.warning("plan_check.quotes_failed", error=str(exc))
            return {}
        return {q.ticker.upper(): q.settled_price for q in quotes}

    async def _states(self, tickers: list[str]) -> dict[str, Mapping[str, str] | None]:
        out: dict[str, Mapping[str, str] | None] = {}
        if self._state is None:
            return out

        async def one(t: str) -> None:
            async with self._sem:
                try:
                    out[t] = await self._state(t)  # type: ignore[misc]
                except Exception as exc:  # noqa: BLE001
                    logger.warning("plan_check.state_failed", ticker=t, error=str(exc))
                    out[t] = None

        await asyncio.gather(*(one(t) for t in tickers))
        return out

    async def run(self, today: date, user_id: str | None = None) -> list[PlanChange]:
        plans = await self._open_plans(user_id)
        tickers = sorted({p.ticker.upper() for p in plans})
        prices, states = await asyncio.gather(self._prices(tickers), self._states(tickers))
        changes: list[PlanChange] = []
        now = datetime.now(UTC)
        for p in plans:
            t = p.ticker.upper()
            ev = evaluate_thesis(p, prices.get(t), today, states.get(t))
            for a, res in zip(p.assumptions, ev.beliefs, strict=True):
                if getattr(a, "check_key", None) and res.result in _STATUS_FROM_RESULT:
                    a.status = _STATUS_FROM_RESULT[res.result]
            old = p.plan_light
            p.plan_light = ev.light
            p.plan_summary = ev.summary
            p.plan_reasons = json.dumps(ev.reasons, ensure_ascii=False)
            p.plan_checked_at = now
            if old != ev.light:
                changes.append(
                    PlanChange(p.id, p.user_id, t, p.title, old, ev.light, ev.summary, ev.reasons)
                )
        await self._session.flush()
        logger.info(
            "plan_check.done",
            plans=len(plans),
            changed=len(changes),
            red=sum(1 for p in plans if p.plan_light == "do"),
        )
        return changes
