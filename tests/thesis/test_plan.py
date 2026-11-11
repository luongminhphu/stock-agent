"""Kế hoạch nắm giữ: tự kiểm điều tôi tin, đèn trạng thái, tóm tắt, gợi ý mức."""

from __future__ import annotations

from datetime import date, timedelta

from src.thesis.plan import (
    BELIEF_FALSE,
    BELIEF_MANUAL,
    BELIEF_TRUE,
    BELIEF_UNKNOWN,
    LIGHT_GREEN,
    LIGHT_GREY,
    LIGHT_RED,
    LIGHT_YELLOW,
    BeliefInput,
    check_belief,
    evaluate_plan,
    suggest_levels,
)

TODAY = date(2026, 11, 11)
GOOD_STATE = {"valuation": "cheap", "earnings": "strong", "trend": "above_both", "range_52w": "mid"}


def _plan(**kw):  # type: ignore[no-untyped-def]
    base = dict(
        closed=False,
        entry_price=100.0,
        stop_price=88.0,
        target_price=125.0,
        review_by=TODAY + timedelta(days=90),
        beliefs=[BeliefInput("Rẻ", "gia_re"), BeliefInput("LN tăng", "loi_nhuan_tang")],
        price=105.0,
        today=TODAY,
        state=GOOD_STATE,
    )
    base.update(kw)
    return evaluate_plan(**base)


def test_check_belief() -> None:
    assert check_belief("gia_re", GOOD_STATE) == BELIEF_TRUE
    assert check_belief("gia_re", {"valuation": "expensive"}) == BELIEF_FALSE
    assert check_belief("gia_re", {"valuation": "fair"}) == BELIEF_UNKNOWN
    assert check_belief("gia_re", None) == BELIEF_UNKNOWN
    assert check_belief(None, GOOD_STATE) == BELIEF_MANUAL


def test_green_with_summary() -> None:
    ev = _plan()
    assert ev.light == LIGHT_GREEN
    assert (
        ev.summary
        == "2/2 điều tôi tin còn đúng · lãi/lỗ +5.0% · cách mức chấp nhận sai 19.3% · còn 90 ngày tới hạn xem lại"
    )


def test_red_when_stop_hit_even_if_beliefs_hold() -> None:
    ev = _plan(price=87.0)
    assert ev.light == LIGHT_RED
    assert "chạm mức chấp nhận sai" in ev.reasons[0]


def test_yellow_one_belief_wrong_red_when_majority_wrong() -> None:
    ev = _plan(state={**GOOD_STATE, "earnings": "declining"})
    assert ev.light == LIGHT_YELLOW and "LN tăng" in ev.reasons[0]
    ev = _plan(state={"valuation": "expensive", "earnings": "declining"})
    assert ev.light == LIGHT_RED


def test_manual_beliefs_use_user_status() -> None:
    ev = _plan(beliefs=[BeliefInput("Tự ghi", None, "invalid")], state=None)
    assert ev.beliefs[0].result == BELIEF_FALSE and ev.light == LIGHT_RED


def test_yellow_reasons_target_review_near_stop_missing_stop() -> None:
    assert "chốt lời" in _plan(price=126.0).reasons[0]
    assert "hạn xem lại" in _plan(review_by=TODAY).reasons[0]
    assert "cách mức chấp nhận sai" in _plan(price=89.0).reasons[0]
    ev = _plan(stop_price=None)
    assert ev.light == LIGHT_YELLOW and "Chưa đặt mức chấp nhận sai" in ev.reasons[-1]


def test_closed_is_grey() -> None:
    assert _plan(closed=True, price=50.0).light == LIGHT_GREY


def test_suggest_levels() -> None:
    s = suggest_levels(30_000, TODAY, typical_drawdown=-0.10, median_return=0.08)
    assert s.stop_price == 26_400 and s.target_price == 32_400
    assert s.review_by == TODAY + timedelta(days=182)
    weak = suggest_levels(30_000, TODAY, median_return=-0.02, p90_return=0.15)
    assert weak.stop_price == 27_000 and weak.target_price == 34_500
    assert "10% trường hợp tốt nhất" in weak.target_note


# ── PlanCheckService (DB) ────────────────────────────────────────────────────

import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.anyio
async def test_plan_check_service_updates_lights_and_beliefs(session) -> None:  # type: ignore[no-untyped-def]
    from src.thesis.models import Assumption, AssumptionStatus, Thesis, ThesisStatus
    from src.thesis.plan_service import PlanCheckService

    t1 = Thesis(
        user_id="u1",
        ticker="HPG",
        title="HPG hồi phục",
        status=ThesisStatus.ACTIVE,
        entry_price=30_000,
        stop_loss=27_000,
        target_price=36_000,
        review_by=TODAY + timedelta(days=100),
    )
    t1.assumptions = [
        Assumption(description="Giá rẻ", check_key="gia_re"),
        Assumption(description="Ngành thép hồi phục", status=AssumptionStatus.VALID),
    ]
    t2 = Thesis(
        user_id="u1",
        ticker="VNM",
        title="VNM",
        status=ThesisStatus.ACTIVE,
        entry_price=70_000,
        stop_loss=65_000,
        review_by=TODAY + timedelta(days=100),
    )
    t3 = Thesis(user_id="u1", ticker="FPT", title="đóng", status=ThesisStatus.CLOSED)
    session.add_all([t1, t2, t3])
    await session.flush()

    class _Quotes:
        async def get_bulk_quotes(self, tickers):  # type: ignore[no-untyped-def]
            px = {"HPG": 31_000.0, "VNM": 64_000.0}
            return [SimpleNamespace(ticker=t, settled_price=px[t]) for t in tickers if t in px]

    async def state(t: str):  # type: ignore[no-untyped-def]
        return {"valuation": "cheap"} if t == "HPG" else None

    changes = await PlanCheckService(session, _Quotes(), state).run(TODAY, "u1")
    assert {c.ticker: c.new_light for c in changes} == {"HPG": "xanh", "VNM": "do"}
    assert t1.assumptions[0].status == AssumptionStatus.VALID
    assert t1.plan_summary.startswith("2/2 điều tôi tin còn đúng")
    assert "chạm mức chấp nhận sai" in json.loads(t2.plan_reasons)[0]
    assert t3.plan_light is None, "kế hoạch đã đóng không nằm trong lượt kiểm tra"

    again = await PlanCheckService(session, _Quotes(), state).run(TODAY, "u1")
    assert again == [], "không đổi đèn → không báo lại"
