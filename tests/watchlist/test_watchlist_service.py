"""Unit tests for WatchlistService.

All DB interactions replaced with AsyncMock repository.
No real DB, no real session.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.watchlist.service import (
    AddToWatchlistInput,
    AlertNotFoundError,
    CreateAlertInput,
    WatchlistItemAlreadyExistsError,
    WatchlistItemNotFoundError,
    WatchlistService,
)
from src.watchlist.models import AlertConditionType, AlertStatus

from tests.watchlist.conftest import make_alert, make_item


def _make_service(mock_repo: AsyncMock) -> WatchlistService:
    svc = WatchlistService.__new__(WatchlistService)
    svc._repo = mock_repo
    return svc


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_new_item_success(mock_repo):
    """Adding a new ticker succeeds when not in watchlist."""
    mock_repo.get_item.return_value = None
    svc = _make_service(mock_repo)

    inp = AddToWatchlistInput(user_id="user-A", ticker="HPG", note="steel play")
    result = await svc.add(inp)

    mock_repo.save_item.assert_awaited_once()
    assert result.ticker == "HPG"
    assert result.user_id == "user-A"


@pytest.mark.asyncio
async def test_add_duplicate_raises(mock_repo):
    """Duplicate ticker raises WatchlistItemAlreadyExistsError."""
    mock_repo.get_item.return_value = make_item(ticker="HPG")
    svc = _make_service(mock_repo)

    with pytest.raises(WatchlistItemAlreadyExistsError, match="HPG"):
        await svc.add(AddToWatchlistInput(user_id="user-A", ticker="HPG"))

    mock_repo.save_item.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_normalises_ticker_to_uppercase(mock_repo):
    """Ticker is stored in uppercase regardless of input."""
    mock_repo.get_item.return_value = None
    svc = _make_service(mock_repo)

    result = await svc.add(AddToWatchlistInput(user_id="user-A", ticker="fpt"))
    assert result.ticker == "FPT"


@pytest.mark.asyncio
async def test_add_with_thesis_id(mock_repo):
    """thesis_id is stored on the item."""
    mock_repo.get_item.return_value = None
    svc = _make_service(mock_repo)

    result = await svc.add(AddToWatchlistInput(user_id="user-A", ticker="VCB", thesis_id=42))
    assert result.thesis_id == 42


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_existing_item(mock_repo):
    item = make_item(ticker="HPG")
    mock_repo.get_item.return_value = item
    svc = _make_service(mock_repo)

    await svc.remove(user_id="user-A", ticker="HPG")
    mock_repo.delete_item.assert_awaited_once_with(item)


@pytest.mark.asyncio
async def test_remove_nonexistent_raises(mock_repo):
    mock_repo.get_item.return_value = None
    svc = _make_service(mock_repo)

    with pytest.raises(WatchlistItemNotFoundError):
        await svc.remove(user_id="user-A", ticker="NOTHERE")

    mock_repo.delete_item.assert_not_awaited()


# ---------------------------------------------------------------------------
# list_items / get_tickers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_items_returns_repo_result(mock_repo):
    items = [make_item("HPG"), make_item("VCB", item_id=2)]
    mock_repo.list_for_user.return_value = items
    svc = _make_service(mock_repo)

    result = await svc.list_items(user_id="user-A")
    assert result == items
    mock_repo.list_for_user.assert_awaited_once_with("user-A")


@pytest.mark.asyncio
async def test_get_tickers_returns_ticker_list(mock_repo):
    mock_repo.list_for_user.return_value = [
        make_item("HPG"),
        make_item("VCB", item_id=2),
        make_item("FPT", item_id=3),
    ]
    svc = _make_service(mock_repo)

    tickers = await svc.get_tickers(user_id="user-A")
    assert set(tickers) == {"HPG", "VCB", "FPT"}


@pytest.mark.asyncio
async def test_list_items_empty(mock_repo):
    mock_repo.list_for_user.return_value = []
    svc = _make_service(mock_repo)

    result = await svc.list_items(user_id="user-A")
    assert result == []


# ---------------------------------------------------------------------------
# update_note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_note_success(mock_repo):
    item = make_item(ticker="HPG", note="old note")
    mock_repo.get_item.return_value = item
    svc = _make_service(mock_repo)

    result = await svc.update_note("user-A", "HPG", "new note")
    assert result.note == "new note"
    mock_repo.save_item.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_note_not_found_raises(mock_repo):
    mock_repo.get_item.return_value = None
    svc = _make_service(mock_repo)

    with pytest.raises(WatchlistItemNotFoundError):
        await svc.update_note("user-A", "HPG", "note")


# ---------------------------------------------------------------------------
# create_alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_alert_with_item_in_watchlist(mock_repo):
    item = make_item(ticker="HPG")
    mock_repo.get_item.return_value = item
    svc = _make_service(mock_repo)

    inp = CreateAlertInput(
        user_id="user-A",
        ticker="HPG",
        condition_type=AlertConditionType.PRICE_ABOVE,
        threshold=32000.0,
    )
    alert = await svc.create_alert(inp)

    mock_repo.save_alert.assert_awaited_once()
    assert alert.ticker == "HPG"
    assert alert.threshold == 32000.0
    assert alert.condition_type == AlertConditionType.PRICE_ABOVE
    assert alert.status == AlertStatus.ACTIVE


@pytest.mark.asyncio
async def test_create_alert_ticker_not_in_watchlist_raises(mock_repo):
    mock_repo.get_item.return_value = None
    svc = _make_service(mock_repo)

    with pytest.raises(WatchlistItemNotFoundError):
        await svc.create_alert(
            CreateAlertInput(
                user_id="user-A",
                ticker="HPG",
                condition_type=AlertConditionType.PRICE_BELOW,
                threshold=20000.0,
            )
        )

    mock_repo.save_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_alert_with_explicit_watchlist_item_id(mock_repo):
    """When watchlist_item_id is provided, repo.get_item is NOT called."""
    svc = _make_service(mock_repo)

    inp = CreateAlertInput(
        user_id="user-A",
        ticker="HPG",
        condition_type=AlertConditionType.CHANGE_PCT_DOWN,
        threshold=5.0,
        watchlist_item_id=99,
    )
    await svc.create_alert(inp)

    mock_repo.get_item.assert_not_awaited()
    mock_repo.save_alert.assert_awaited_once()


# ---------------------------------------------------------------------------
# dismiss_alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dismiss_alert_success(mock_repo):
    alert = make_alert(alert_id=5, status=AlertStatus.ACTIVE)
    mock_repo.list_active_alerts.return_value = [alert]
    svc = _make_service(mock_repo)

    await svc.dismiss_alert(alert_id=5, user_id="user-A")

    assert alert.status == AlertStatus.DISMISSED
    mock_repo.save_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_dismiss_alert_not_found_raises(mock_repo):
    mock_repo.list_active_alerts.return_value = []
    svc = _make_service(mock_repo)

    with pytest.raises(AlertNotFoundError):
        await svc.dismiss_alert(alert_id=99, user_id="user-A")


@pytest.mark.asyncio
async def test_list_active_alerts(mock_repo):
    alerts = [make_alert(alert_id=1), make_alert(alert_id=2)]
    mock_repo.list_active_alerts.return_value = alerts
    svc = _make_service(mock_repo)

    result = await svc.list_active_alerts(user_id="user-A")
    assert len(result) == 2
    mock_repo.list_active_alerts.assert_awaited_once_with("user-A")
