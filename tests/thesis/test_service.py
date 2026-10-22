"""Unit tests for ThesisService.

Covers: create, list_for_user, close, invalidate, guard against
double-close, and ownership isolation.
"""

from __future__ import annotations

import pytest

from src.thesis.models import ThesisStatus
from src.thesis.service import (
    CreateThesisInput,
    ThesisAlreadyClosedError,
    ThesisConflictError,
    ThesisNotFoundError,
    ThesisService,
    UpdateThesisInput,
)

USER_A = "investor_alice"
USER_B = "investor_bob"


def _inp(
    user_id: str = USER_A,
    ticker: str = "HPG",
    title: str = "Steel cycle recovery",
    entry_price: float = 20_000,
    target_price: float = 30_000,
    stop_loss: float = 17_000,
) -> CreateThesisInput:
    return CreateThesisInput(
        user_id=user_id,
        ticker=ticker,
        title=title,
        entry_price=entry_price,
        target_price=target_price,
        stop_loss=stop_loss,
    )


async def test_create_returns_thesis(session):
    svc = ThesisService(session)
    thesis = await svc.create(_inp())
    await session.flush()

    assert thesis.id is not None
    assert thesis.ticker == "HPG"
    assert thesis.status == ThesisStatus.ACTIVE


async def test_create_normalises_ticker(session):
    svc = ThesisService(session)
    thesis = await svc.create(_inp(ticker="vnm"))
    await session.flush()

    assert thesis.ticker == "VNM"


async def test_upside_pct_computed(session):
    svc = ThesisService(session)
    thesis = await svc.create(_inp(entry_price=20_000, target_price=25_000))
    await session.flush()

    assert thesis.upside_pct == pytest.approx(25.0)


async def test_risk_reward_computed(session):
    # upside=10k, downside=3k → R/R = 10/3 ≈ 3.33
    svc = ThesisService(session)
    thesis = await svc.create(_inp(entry_price=20_000, target_price=30_000, stop_loss=17_000))
    await session.flush()

    assert thesis.risk_reward == pytest.approx(10_000 / 3_000, rel=1e-2)


async def test_list_for_user_returns_only_own(session):
    svc = ThesisService(session)
    await svc.create(_inp(user_id=USER_A, ticker="HPG"))
    await svc.create(_inp(user_id=USER_B, ticker="VNM"))
    await session.flush()

    alice = await svc.list_for_user(USER_A)
    bob = await svc.list_for_user(USER_B)

    assert len(alice) == 1 and alice[0].ticker == "HPG"
    assert len(bob) == 1 and bob[0].ticker == "VNM"


async def test_list_for_user_filter_by_status(session):
    svc = ThesisService(session)
    t1 = await svc.create(_inp(ticker="HPG"))
    await svc.create(_inp(ticker="VNM"))
    await session.flush()

    await svc.close(thesis_id=t1.id, user_id=USER_A)
    await session.flush()

    active = await svc.list_for_user(USER_A, status=ThesisStatus.ACTIVE)
    closed = await svc.list_for_user(USER_A, status=ThesisStatus.CLOSED)

    assert len(active) == 1 and active[0].ticker == "VNM"
    assert len(closed) == 1 and closed[0].ticker == "HPG"


async def test_close_sets_status(session):
    svc = ThesisService(session)
    thesis = await svc.create(_inp())
    await session.flush()

    closed = await svc.close(thesis_id=thesis.id, user_id=USER_A)
    assert closed.status == ThesisStatus.CLOSED
    assert closed.closed_at is not None


async def test_invalidate_sets_status(session):
    svc = ThesisService(session)
    thesis = await svc.create(_inp())
    await session.flush()

    inv = await svc.invalidate(thesis_id=thesis.id, user_id=USER_A)
    assert inv.status == ThesisStatus.INVALIDATED


async def test_close_already_closed_raises(session):
    svc = ThesisService(session)
    thesis = await svc.create(_inp())
    await session.flush()
    await svc.close(thesis_id=thesis.id, user_id=USER_A)
    await session.flush()

    with pytest.raises(ThesisAlreadyClosedError):
        await svc.close(thesis_id=thesis.id, user_id=USER_A)


async def test_get_wrong_user_raises(session):
    svc = ThesisService(session)
    thesis = await svc.create(_inp(user_id=USER_A))
    await session.flush()

    with pytest.raises(ThesisNotFoundError):
        await svc.get(thesis_id=thesis.id, user_id=USER_B)


async def test_create_with_assumptions_and_catalysts(session):
    svc = ThesisService(session)
    thesis = await svc.create(
        CreateThesisInput(
            user_id=USER_A,
            ticker="MSN",
            title="Masan consumer rerating",
            assumptions=["Inflation subsides", "MCH margins recover"],
            catalysts=["Q3 earnings beat"],
        )
    )
    await session.flush()

    assert len(thesis.assumptions) == 2
    assert len(thesis.catalysts) == 1
    assert thesis.assumptions[0].description == "Inflation subsides"


# ---------------------------------------------------------------------------
# T1 — reactivate (mở lại thesis)
# ---------------------------------------------------------------------------


async def test_reactivate_closed_thesis_returns_to_active(session):
    svc = ThesisService(session)
    thesis = await svc.create(_inp())
    await session.flush()
    await svc.close(thesis_id=thesis.id, user_id=USER_A)

    reopened = await svc.reactivate(thesis_id=thesis.id, user_id=USER_A)
    assert reopened.status == ThesisStatus.ACTIVE
    assert reopened.closed_at is None
    # Sửa lại được sau khi mở lại
    updated = await svc.update(thesis.id, USER_A, UpdateThesisInput(title="Reopened"))
    assert updated.title == "Reopened"


async def test_reactivate_invalidated_and_paused(session):
    svc = ThesisService(session)
    a = await svc.create(_inp(ticker="VNM"))
    b = await svc.create(_inp(ticker="FPT"))
    await session.flush()
    await svc.invalidate(thesis_id=a.id, user_id=USER_A)
    b.status = ThesisStatus.PAUSED
    await session.flush()

    assert (await svc.reactivate(a.id, USER_A)).status == ThesisStatus.ACTIVE
    assert (await svc.reactivate(b.id, USER_A)).status == ThesisStatus.ACTIVE


async def test_reactivate_active_is_idempotent(session):
    svc = ThesisService(session)
    thesis = await svc.create(_inp())
    await session.flush()
    same = await svc.reactivate(thesis.id, USER_A)
    assert same.id == thesis.id and same.status == ThesisStatus.ACTIVE


async def test_reactivate_blocked_when_other_open_thesis_same_ticker(session):
    svc = ThesisService(session)
    old = await svc.create(_inp(title="Old"))
    await session.flush()
    await svc.close(thesis_id=old.id, user_id=USER_A)
    new = await svc.create(_inp(title="New"))
    await session.flush()

    with pytest.raises(ThesisConflictError, match=f"#{new.id}"):
        await svc.reactivate(old.id, USER_A)
    # WEAKENING cũng tính là đang mở
    new.status = ThesisStatus.WEAKENING
    await session.flush()
    with pytest.raises(ThesisConflictError):
        await svc.reactivate(old.id, USER_A)


async def test_reactivate_wrong_user_raises_not_found(session):
    svc = ThesisService(session)
    thesis = await svc.create(_inp())
    await session.flush()
    await svc.close(thesis_id=thesis.id, user_id=USER_A)
    with pytest.raises(ThesisNotFoundError):
        await svc.reactivate(thesis.id, USER_B)


async def test_update_closed_thesis_error_message_is_readable(session):
    svc = ThesisService(session)
    thesis = await svc.create(_inp())
    await session.flush()
    await svc.close(thesis_id=thesis.id, user_id=USER_A)
    with pytest.raises(ThesisAlreadyClosedError) as ei:
        await svc.update(thesis.id, USER_A, UpdateThesisInput(title="x"))
    msg = str(ei.value)
    assert f"#{thesis.id}" in msg and "closed" in msg and "mở lại" in msg
