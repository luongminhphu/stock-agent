"""Unit tests for ScanService signal aggregation (no DB, mock QuoteService)."""
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.watchlist.models import Alert, AlertConditionType, AlertStatus, WatchlistItem
from src.watchlist.scan_service import ScanService, ScanServiceNotConfiguredError


def _make_mock_item(ticker: str, alerts: list[Alert]) -> WatchlistItem:
    item = MagicMock(spec=WatchlistItem)
    item.ticker = ticker
    item.alerts = alerts
    return item


def _make_alert(condition: AlertConditionType, threshold: float) -> Alert:
    a = Alert.__new__(Alert)
    a.condition_type = condition
    a.threshold = threshold
    a.status = AlertStatus.ACTIVE
    return a


@dataclass
class FakeQuote:
    ticker: str
    price: float
    change_pct: float


async def test_scan_raises_without_quote_service() -> None:
    svc = ScanService(session=MagicMock(), quote_service=None)
    with pytest.raises(ScanServiceNotConfiguredError):
        await svc.scan_user("user1")


async def test_scan_detects_triggered_alert() -> None:
    alert = _make_alert(AlertConditionType.PRICE_ABOVE, threshold=80_000)
    item = _make_mock_item("VNM", [alert])

    mock_quote_service = MagicMock()
    mock_quote_service.get_quote = AsyncMock(
        return_value=FakeQuote(ticker="VNM", price=85_000, change_pct=1.5)
    )

    mock_session = MagicMock()
    svc = ScanService(session=mock_session, quote_service=mock_quote_service)

    with patch.object(svc._repo, "list_for_user", AsyncMock(return_value=[item])):
        result = await svc.scan_user("user1")

    assert result.triggered_count == 1
    assert "VNM" in result.tickers_with_signals


async def test_scan_no_signals_when_below_threshold() -> None:
    alert = _make_alert(AlertConditionType.PRICE_ABOVE, threshold=100_000)
    item = _make_mock_item("HPG", [alert])

    mock_quote_service = MagicMock()
    mock_quote_service.get_quote = AsyncMock(
        return_value=FakeQuote(ticker="HPG", price=85_000, change_pct=0.5)
    )

    mock_session = MagicMock()
    svc = ScanService(session=mock_session, quote_service=mock_quote_service)

    with patch.object(svc._repo, "list_for_user", AsyncMock(return_value=[item])):
        result = await svc.scan_user("user1")

    assert result.triggered_count == 0
    assert result.tickers_with_signals == []
