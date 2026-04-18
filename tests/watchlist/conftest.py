"""Shared fixtures for watchlist segment unit tests."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.watchlist.models import (
    Alert,
    AlertConditionType,
    AlertStatus,
    WatchlistItem,
)


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def make_item(
    ticker: str = "HPG",
    user_id: str = "user-test-001",
    note: str | None = None,
    thesis_id: int | None = None,
    item_id: int = 1,
) -> WatchlistItem:
    item = WatchlistItem(
        user_id=user_id,
        ticker=ticker,
        note=note,
        thesis_id=thesis_id,
        priority=100,
    )
    item.id = item_id
    item.added_at = datetime.now(timezone.utc)
    item.updated_at = datetime.now(timezone.utc)
    item.alerts = []
    item.reminder = None
    return item


def make_alert(
    ticker: str = "HPG",
    condition_type: AlertConditionType = AlertConditionType.PRICE_ABOVE,
    threshold: float = 30000.0,
    status: AlertStatus = AlertStatus.ACTIVE,
    user_id: str = "user-test-001",
    alert_id: int = 1,
    watchlist_item_id: int = 1,
) -> Alert:
    alert = Alert(
        user_id=user_id,
        ticker=ticker,
        condition_type=condition_type,
        threshold=threshold,
        status=status,
        watchlist_item_id=watchlist_item_id,
    )
    alert.id = alert_id
    alert.triggered_at = None
    alert.triggered_price = None
    alert.note = None
    alert.created_at = datetime.now(timezone.utc)
    return alert


# ---------------------------------------------------------------------------
# Mock repo / quote_service fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_repo():
    repo = AsyncMock()
    repo.get_item.return_value = None
    repo.list_for_user.return_value = []
    repo.save_item.return_value = None
    repo.delete_item.return_value = None
    repo.save_alert.return_value = None
    repo.list_active_alerts.return_value = []
    return repo


@pytest.fixture()
def mock_quote_service():
    qs = AsyncMock()
    quote = MagicMock()
    quote.price = 29000.0
    quote.change_pct = 2.0
    qs.get_quote.return_value = quote
    return qs
