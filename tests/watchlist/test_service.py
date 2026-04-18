"""Unit tests for WatchlistService (no DB — mocked repository)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.watchlist.models import AlertConditionType, WatchlistItem
from src.watchlist.service import (
    AddToWatchlistInput,
    CreateAlertInput,
    WatchlistItemAlreadyExistsError,
    WatchlistItemNotFoundError,
    WatchlistService,
)


def _make_service() -> WatchlistService:
    session = MagicMock()
    return WatchlistService(session=session)


async def test_add_new_item() -> None:
    svc = _make_service()
    svc._repo.get_item = AsyncMock(return_value=None)
    svc._repo.save_item = AsyncMock(side_effect=lambda x: x)

    item = await svc.add(AddToWatchlistInput(user_id="u1", ticker="VNM"))
    assert item.ticker == "VNM"
    assert item.user_id == "u1"


async def test_add_duplicate_raises() -> None:
    svc = _make_service()
    existing = MagicMock(spec=WatchlistItem)
    svc._repo.get_item = AsyncMock(return_value=existing)

    with pytest.raises(WatchlistItemAlreadyExistsError):
        await svc.add(AddToWatchlistInput(user_id="u1", ticker="VNM"))


async def test_remove_not_found_raises() -> None:
    svc = _make_service()
    svc._repo.get_item = AsyncMock(return_value=None)

    with pytest.raises(WatchlistItemNotFoundError):
        await svc.remove(user_id="u1", ticker="XYZ")


async def test_get_tickers_returns_list() -> None:
    svc = _make_service()
    item1 = MagicMock(spec=WatchlistItem)
    item1.ticker = "VNM"
    item2 = MagicMock(spec=WatchlistItem)
    item2.ticker = "FPT"
    svc._repo.list_for_user = AsyncMock(return_value=[item1, item2])

    tickers = await svc.get_tickers("u1")
    assert tickers == ["VNM", "FPT"]
