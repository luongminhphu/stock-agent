"""Unit tests for WatchlistService.

Covers: add, remove, list, duplicate guard, add_alert, dismiss_alert.
All DB ops use the in-memory SQLite fixture from conftest.py.
"""
from __future__ import annotations

import pytest

from src.watchlist.models import AlertConditionType, AlertStatus
from src.watchlist.service import (
    AddAlertInput,
    AddToWatchlistInput,
    WatchlistItemAlreadyExistsError,
    WatchlistItemNotFoundError,
    WatchlistService,
)

USER = "user_123"


async def test_add_and_list(session):
    svc = WatchlistService(session)
    await svc.add(AddToWatchlistInput(user_id=USER, ticker="VNM", note="dairy play"))
    await session.flush()

    items = await svc.list_items(USER)
    assert len(items) == 1
    assert items[0].ticker == "VNM"
    assert items[0].note == "dairy play"


async def test_add_normalises_ticker(session):
    svc = WatchlistService(session)
    await svc.add(AddToWatchlistInput(user_id=USER, ticker="vnm"))
    await session.flush()

    items = await svc.list_items(USER)
    assert items[0].ticker == "VNM"


async def test_add_duplicate_raises(session):
    svc = WatchlistService(session)
    await svc.add(AddToWatchlistInput(user_id=USER, ticker="HPG"))
    await session.flush()

    with pytest.raises(WatchlistItemAlreadyExistsError):
        await svc.add(AddToWatchlistInput(user_id=USER, ticker="HPG"))


async def test_remove(session):
    svc = WatchlistService(session)
    await svc.add(AddToWatchlistInput(user_id=USER, ticker="FPT"))
    await session.flush()

    await svc.remove(user_id=USER, ticker="FPT")
    await session.flush()

    items = await svc.list_items(USER)
    assert items == []


async def test_remove_not_found_raises(session):
    svc = WatchlistService(session)
    with pytest.raises(WatchlistItemNotFoundError):
        await svc.remove(user_id=USER, ticker="DOESNOTEXIST")


async def test_add_alert(session):
    svc = WatchlistService(session)
    await svc.add(AddToWatchlistInput(user_id=USER, ticker="VIC"))
    await session.flush()

    alert = await svc.add_alert(
        AddAlertInput(
            user_id=USER,
            ticker="VIC",
            condition_type=AlertConditionType.PRICE_ABOVE,
            threshold=60_000,
        )
    )
    await session.flush()

    assert alert.id is not None
    assert alert.status == AlertStatus.ACTIVE
    assert alert.threshold == 60_000


async def test_add_alert_requires_watchlist_item(session):
    svc = WatchlistService(session)
    with pytest.raises(WatchlistItemNotFoundError):
        await svc.add_alert(
            AddAlertInput(
                user_id=USER,
                ticker="NOTINLIST",
                condition_type=AlertConditionType.PRICE_BELOW,
                threshold=10_000,
            )
        )


async def test_dismiss_alert(session):
    svc = WatchlistService(session)
    await svc.add(AddToWatchlistInput(user_id=USER, ticker="MSN"))
    await session.flush()

    alert = await svc.add_alert(
        AddAlertInput(
            user_id=USER,
            ticker="MSN",
            condition_type=AlertConditionType.CHANGE_PCT_UP,
            threshold=5.0,
        )
    )
    await session.flush()

    await svc.dismiss_alert(alert_id=alert.id, user_id=USER)
    await session.flush()

    active = await svc.list_active_alerts(USER)
    assert not any(a.id == alert.id for a in active)


async def test_list_items_isolated_per_user(session):
    svc = WatchlistService(session)
    await svc.add(AddToWatchlistInput(user_id="alice", ticker="VNM"))
    await svc.add(AddToWatchlistInput(user_id="bob", ticker="HPG"))
    await session.flush()

    alice_items = await svc.list_items("alice")
    bob_items = await svc.list_items("bob")

    assert [i.ticker for i in alice_items] == ["VNM"]
    assert [i.ticker for i in bob_items] == ["HPG"]
