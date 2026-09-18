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

from src.thesis.stop_breach_service import StopBreachService


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
        ticker="HPG", qty=8000.0, locked_qty=3000.0,
        locked_reason="esop", locked_until=datetime.date(2026, 12, 31),
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
    from src.thesis.models import Thesis, ThesisStatus

    thesis = SimpleNamespace(
        id=7, user_id="u1", ticker="HPG", stop_loss=23000.0,
        score=40.0, status=ThesisStatus.ACTIVE, updated_at=None,
        assumptions=[], closed_at=None,
    )
    pos = SimpleNamespace(
        ticker="HPG", qty=8000.0, locked_qty=8000.0,
        locked_reason="esop", locked_until=None,
    )
    sess = _session_with([pos], theses=[thesis])
    svc = StopBreachService(
        session=sess, quote_service=_quote_service(21000.0),
        detector=None, enabled=False,   # observe-only: không AI, không mutate
    )

    # Trực tiếp gọi _process với lock để tránh quét scheme đầy đủ của scan()
    out = await svc._process(thesis, price=21000.0, lock=pos)
    assert out is not None
    assert out.locked_qty == 8000.0
    assert out.sellable_qty == 0.0
    assert out.locked_reason == "esop"
