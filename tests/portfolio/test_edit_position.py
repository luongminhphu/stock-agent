"""edit_position must persist qty/avg_cost and write a PositionEdit audit row.

Covers the "Sửa trực tiếp" path end-to-end at the service layer: validation,
open-position lookup, mutation, and the audit record that GET /portfolio/trades
merges into the timeline.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.portfolio.service import (
    PortfolioService,
    PositionNotFoundError,
)


def _make_service(open_position: object | None) -> tuple[PortfolioService, AsyncMock]:
    session = AsyncMock()
    repo = MagicMock()
    repo.get_open_position = AsyncMock(return_value=open_position)
    repo.save_position = AsyncMock()
    repo.save_position_edit = AsyncMock()
    svc = PortfolioService.__new__(PortfolioService)
    svc._session = session
    svc._repo = repo
    return svc, repo


def _pos(**kwargs: object) -> SimpleNamespace:
    defaults = dict(
        id=7,
        user_id="u1",
        ticker="FPT",
        qty=1000.0,
        avg_cost=80_000.0,
        locked_qty=0.0,
        locked_reason=None,
        locked_until=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_edit_updates_qty_and_avg_and_writes_audit():
    svc, repo = _make_service(_pos())

    result = await svc.edit_position(user_id="u1", ticker="FPT", qty=1500.0, avg_cost=75_000.0)

    assert result.qty == 1500.0
    assert result.avg_cost == 75_000.0
    repo.save_position.assert_awaited_once()
    edit = repo.save_position_edit.await_args.args[0]
    assert edit.old_qty == 1000.0
    assert edit.new_qty == 1500.0
    assert edit.old_avg_cost == 80_000.0
    assert edit.new_avg_cost == 75_000.0
    assert edit.position_id == 7


@pytest.mark.asyncio
async def test_edit_qty_only_keeps_avg():
    svc, repo = _make_service(_pos())

    result = await svc.edit_position(user_id="u1", ticker="fpt", qty=2000.0)

    assert result.qty == 2000.0
    assert result.avg_cost == 80_000.0  # unchanged
    edit = repo.save_position_edit.await_args.args[0]
    assert edit.new_avg_cost == 80_000.0


@pytest.mark.asyncio
async def test_edit_avg_only_keeps_qty():
    svc, repo = _make_service(_pos())

    result = await svc.edit_position(user_id="u1", ticker="FPT", avg_cost=90_000.0)

    assert result.qty == 1000.0  # unchanged
    assert result.avg_cost == 90_000.0


@pytest.mark.asyncio
async def test_edit_requires_at_least_one_field():
    svc, _ = _make_service(_pos())
    with pytest.raises(ValueError, match="it nhat mot"):
        await svc.edit_position(user_id="u1", ticker="FPT")


@pytest.mark.asyncio
async def test_edit_rejects_non_positive_values():
    svc, _ = _make_service(_pos())
    with pytest.raises(ValueError):
        await svc.edit_position(user_id="u1", ticker="FPT", qty=0)
    with pytest.raises(ValueError):
        await svc.edit_position(user_id="u1", ticker="FPT", avg_cost=-5)


@pytest.mark.asyncio
async def test_edit_raises_when_no_open_position():
    svc, _ = _make_service(None)
    with pytest.raises(PositionNotFoundError):
        await svc.edit_position(user_id="u1", ticker="VCB", qty=100.0)


# ---------------------------------------------------------------------------
# Wave 9.1 — locked position (ESOP / phat hanh them khong ban duoc)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_locked_only_sets_lock_and_audits():
    svc, repo = _make_service(_pos())

    result = await svc.edit_position(
        user_id="u1",
        ticker="FPT",
        locked_qty=400.0,
        locked_reason=" ESOP ",
        locked_until=date(2027, 3, 1),
    )

    assert result.locked_qty == 400.0
    assert result.locked_reason == "esop"  # normalize strip + lower
    assert result.locked_until == date(2027, 3, 1)
    assert result.qty == 1000.0  # qty/avg khong doi
    edit = repo.save_position_edit.await_args.args[0]
    assert edit.old_locked_qty == 0.0
    assert edit.new_locked_qty == 400.0
    assert edit.new_locked_reason == "esop"
    assert edit.new_locked_until == date(2027, 3, 1)


@pytest.mark.asyncio
async def test_edit_qty_only_leaves_locked_audit_null():
    """Edit qty/avg thuan → locked_* audit = NULL (edit khong dung toi lock)."""
    svc, repo = _make_service(_pos())

    await svc.edit_position(user_id="u1", ticker="FPT", qty=1500.0)

    edit = repo.save_position_edit.await_args.args[0]
    assert edit.old_locked_qty is None
    assert edit.new_locked_qty is None
    assert edit.new_locked_reason is None
    assert edit.new_locked_until is None


@pytest.mark.asyncio
async def test_locked_qty_cannot_exceed_qty():
    svc, _ = _make_service(_pos())
    with pytest.raises(ValueError, match="khong the lon hon"):
        await svc.edit_position(user_id="u1", ticker="FPT", locked_qty=1001.0)


@pytest.mark.asyncio
async def test_locked_qty_can_equal_qty_full_lock():
    svc, _ = _make_service(_pos())
    result = await svc.edit_position(user_id="u1", ticker="FPT", locked_qty=1000.0)
    assert result.locked_qty == 1000.0


@pytest.mark.asyncio
async def test_locked_qty_zero_unlocks_and_clears_reason_until():
    svc, _ = _make_service(
        _pos(locked_qty=400.0, locked_reason="esop", locked_until=date(2027, 3, 1))
    )

    result = await svc.edit_position(user_id="u1", ticker="FPT", locked_qty=0.0)

    assert result.locked_qty == 0.0
    assert result.locked_reason is None
    assert result.locked_until is None


@pytest.mark.asyncio
async def test_locked_qty_negative_rejected():
    svc, _ = _make_service(_pos())
    with pytest.raises(ValueError, match="locked_qty phai >= 0"):
        await svc.edit_position(user_id="u1", ticker="FPT", locked_qty=-1.0)


@pytest.mark.asyncio
async def test_locked_qty_validated_against_new_qty_same_call():
    """Sua qty va locked trong cung lenh: locked <= qty moi."""
    svc, _ = _make_service(_pos())

    result = await svc.edit_position(user_id="u1", ticker="FPT", qty=500.0, locked_qty=500.0)
    assert result.qty == 500.0
    assert result.locked_qty == 500.0

    svc2, _ = _make_service(_pos())
    with pytest.raises(ValueError, match="khong the lon hon"):
        await svc2.edit_position(user_id="u1", ticker="FPT", qty=500.0, locked_qty=600.0)
