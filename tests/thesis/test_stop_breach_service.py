"""Tests cho StopBreachService — Wave 9.4 phần position-lock enrichment.

Không chạm AI detector (rule-only mode: detector=None) và dùng fake
quote_service — chỉ verify:
  1. _load_position_locks map đúng ticker → Position có locked.
  2. Outcome mang locked_qty/sellable_qty khi vị thế khóa.
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.thesis.price_snapshot import PriceSnapshot
from src.thesis.stop_breach_service import NEAR_STOP_ATR, StopBreachService


def _session_with(positions: list, theses: list):
    """Tạo AsyncSession giả lập trả về rows tùy theo model trong query."""
    sess = AsyncMock()

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            class _S:
                def __init__(self, rows):
                    self._r = rows

                def all(self):
                    return self._r

            return _S(self._rows)

    async def _execute(stmt):
        text = str(stmt)
        if "positions" in text:
            return _Result(positions)
        return _Result(theses)

    sess.execute = AsyncMock(side_effect=_execute)
    sess.flush = AsyncMock()
    return sess


def _quote_service(price: float):
    svc = SimpleNamespace()
    svc.get_quote = AsyncMock(return_value=SimpleNamespace(price=price))
    return svc


@pytest.mark.anyio
async def test_load_position_locks_maps_ticker():
    pos = SimpleNamespace(
        ticker="HPG",
        qty=8000.0,
        locked_qty=3000.0,
        locked_reason="esop",
        locked_until=datetime.date(2026, 12, 31),
    )
    sess = _session_with([pos], theses=[])
    svc = StopBreachService(session=sess, quote_service=_quote_service(20000.0))

    m = await svc._load_position_locks("u1", ["HPG", "VNM"])
    assert list(m.keys()) == ["HPG"]
    assert m["HPG"].locked_qty == 3000.0


@pytest.mark.anyio
async def test_outcome_carries_lock_fields():
    """Khi _process gặp thesis breach và lock object được truyền, outcome
    phải mang đủ locked_qty/sellable_qty để downstream đổi messaging."""
    from src.thesis.models import ThesisStatus

    thesis = SimpleNamespace(
        id=7,
        user_id="u1",
        ticker="HPG",
        stop_loss=23000.0,
        score=40.0,
        status=ThesisStatus.ACTIVE,
        updated_at=None,
        assumptions=[],
        closed_at=None,
    )
    pos = SimpleNamespace(
        ticker="HPG",
        qty=8000.0,
        locked_qty=8000.0,
        locked_reason="esop",
        locked_until=None,
    )
    sess = _session_with([pos], theses=[thesis])
    svc = StopBreachService(
        session=sess,
        quote_service=_quote_service(21000.0),
        detector=None,
        enabled=False,  # observe-only: không AI, không mutate
    )

    # Trực tiếp gọi _process với lock để tránh quét scheme đầy đủ của scan()
    out = await svc._process(thesis, PriceSnapshot(price=21000.0), lock=pos)
    assert out is not None
    assert out.locked_qty == 8000.0
    assert out.sellable_qty == 0.0
    assert out.locked_reason == "esop"


# ---------------------------------------------------------------------------
# Wave C3 — bulk TickerContext + near_stop theo ATR
# ---------------------------------------------------------------------------


def _thesis(**kw):
    from src.thesis.models import ThesisStatus

    base = dict(
        id=7,
        user_id="u1",
        ticker="HPG",
        stop_loss=23000.0,
        score=40.0,
        status=ThesisStatus.ACTIVE,
        updated_at=None,
        assumptions=[],
        closed_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _ctx(price: float, atr14: float | None, quality: str = "live"):
    return SimpleNamespace(price=price, atr14=atr14, source_quality=quality)


@pytest.mark.anyio
async def test_scan_prefers_ticker_context_bulk_and_skips_get_quote():
    thesis = _thesis()
    qs = _quote_service(21000.0)
    tcs = SimpleNamespace(get_many=AsyncMock(return_value={"HPG": _ctx(21000.0, 500.0)}))
    svc = StopBreachService(
        session=_session_with([], theses=[thesis]),
        quote_service=qs,
        detector=None,
        enabled=False,
        ticker_context_service=tcs,
    )
    res = await svc.scan("u1")
    tcs.get_many.assert_awaited_once_with(["HPG"])
    qs.get_quote.assert_not_awaited()
    assert len(res.outcomes) == 1
    out = res.outcomes[0]
    assert out.action == "observed"
    assert out.stop_distance_atr == -4.0  # (21000-23000)/500
    assert out.source_quality == "live"


@pytest.mark.anyio
async def test_scan_falls_back_to_get_quote_when_context_missing_ticker():
    thesis = _thesis()
    qs = _quote_service(21000.0)
    tcs = SimpleNamespace(get_many=AsyncMock(return_value={}))
    svc = StopBreachService(
        session=_session_with([], theses=[thesis]),
        quote_service=qs,
        detector=None,
        enabled=False,
        ticker_context_service=tcs,
    )
    res = await svc.scan("u1")
    qs.get_quote.assert_awaited_once_with("HPG")
    assert res.outcomes[0].stop_distance_atr is None  # get_quote không có ATR
    assert res.outcomes[0].source_quality == "quote"


@pytest.mark.anyio
async def test_near_stop_emitted_when_within_one_atr():
    """Giá 23,300 / stop 23,000 / ATR 500 → distance 0.6 ATR → near_stop, không breach."""
    thesis = _thesis()
    svc = StopBreachService(
        session=_session_with([], theses=[thesis]),
        quote_service=_quote_service(23300.0),
        detector=None,
        enabled=True,
    )
    out = await svc._process(
        thesis, PriceSnapshot(price=23300.0, atr14=500.0, source_quality="live")
    )
    assert out is not None and out.action == "near_stop"
    assert out.stop_distance_atr == 0.6
    assert out.overshoot_pct < 0  # âm = còn cách stop
    assert 0 < out.stop_distance_atr < NEAR_STOP_ATR


@pytest.mark.anyio
async def test_no_near_stop_when_far_or_without_atr():
    thesis = _thesis()
    svc = StopBreachService(
        session=_session_with([], theses=[thesis]),
        quote_service=_quote_service(25000.0),
        detector=None,
        enabled=True,
    )
    # xa stop (4 ATR)
    assert await svc._process(thesis, PriceSnapshot(price=25000.0, atr14=500.0)) is None
    # gần stop nhưng không có ATR (fallback) → không suy diễn
    assert await svc._process(thesis, PriceSnapshot(price=23300.0, atr14=None)) is None


@pytest.mark.anyio
async def test_near_stop_respects_cooldown():
    thesis = _thesis(updated_at=datetime.datetime.now(datetime.UTC))
    svc = StopBreachService(
        session=_session_with([], theses=[thesis]),
        quote_service=_quote_service(23300.0),
        detector=None,
        enabled=True,
        cooldown_hours=24.0,
    )
    assert await svc._process(thesis, PriceSnapshot(price=23300.0, atr14=500.0)) is None


def test_scan_result_groups_near_stop_separately():
    from src.thesis.stop_breach_service import StopBreachOutcome, StopBreachScanResult

    def _o(action):
        return StopBreachOutcome(
            thesis_id=1, ticker="HPG", current_price=1, stop_loss=1, overshoot_pct=0, action=action
        )

    r = StopBreachScanResult(outcomes=[_o("invalidated"), _o("observed"), _o("near_stop")])
    assert [o.action for o in r.near_stop] == ["near_stop"]
    assert [o.action for o in r.observed] == ["observed"]
    assert {o.action for o in r.breached} == {"invalidated", "observed"}
