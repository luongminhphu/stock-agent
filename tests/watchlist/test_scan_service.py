"""Unit tests for ScanService.

quote_service is AsyncMock. Repository is AsyncMock.
Tests:
- scan_user happy path (signals + no-signal tickers)
- ticker error is captured, not raised
- no quote_service raises ScanServiceNotConfiguredError
- triggered alerts are recorded in the signal
- signal_type classification
- ScanResult aggregation properties
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.watchlist.models import AlertConditionType, AlertStatus
from src.watchlist.scan_service import (
    ScanResult,
    ScanService,
    ScanServiceNotConfiguredError,
    ScanSignal,
)
from tests.watchlist.conftest import make_alert, make_item


def _make_quote(price: float = 29000.0, change_pct: float = 2.0) -> MagicMock:
    q = MagicMock()
    q.price = price
    q.change_pct = change_pct
    q.volume_ratio = 1.0
    return q


def _make_service(mock_repo: AsyncMock, mock_qs: AsyncMock | None) -> ScanService:
    session = AsyncMock()
    session.add = MagicMock()  # sync in SQLAlchemy
    svc = ScanService(session=session, quote_service=mock_qs)
    svc._repo = mock_repo
    svc._signal_event_repo = AsyncMock()
    svc._signal_event_repo.has_recent_signal.return_value = False
    svc._persist_snapshot = AsyncMock()  # type: ignore[method-assign]
    return svc


# ---------------------------------------------------------------------------
# ScanServiceNotConfiguredError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_without_quote_service_raises(mock_repo):
    svc = _make_service(mock_repo, None)
    with pytest.raises(ScanServiceNotConfiguredError):
        await svc.scan_user(user_id="user-A")


# ---------------------------------------------------------------------------
# Empty watchlist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_empty_watchlist(mock_repo, mock_quote_service):
    mock_repo.list_for_user.return_value = []
    svc = _make_service(mock_repo, mock_quote_service)

    result = await svc.scan_user(user_id="user-A")

    assert result.signals == []
    assert result.errors == {}
    assert result.triggered_count == 0
    mock_quote_service.get_quote.assert_not_awaited()


# ---------------------------------------------------------------------------
# Signals only included when alert triggered OR strong move
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_small_move_no_alert_excluded(mock_repo, mock_quote_service):
    """Ticker with <5% move and no triggered alerts is excluded from signals."""
    item = make_item(ticker="HPG")
    mock_repo.list_for_user.return_value = [item]
    mock_quote_service.get_quote.return_value = _make_quote(price=29000.0, change_pct=1.0)
    svc = _make_service(mock_repo, mock_quote_service)

    result = await svc.scan_user(user_id="user-A")
    assert result.signals == []


@pytest.mark.asyncio
async def test_scan_strong_move_included(mock_repo, mock_quote_service):
    """Ticker with >=5% move is included even without alert."""
    item = make_item(ticker="HPG")
    mock_repo.list_for_user.return_value = [item]
    mock_quote_service.get_quote.return_value = _make_quote(price=29000.0, change_pct=6.5)
    svc = _make_service(mock_repo, mock_quote_service)

    result = await svc.scan_user(user_id="user-A")
    assert len(result.signals) == 1
    assert result.signals[0].ticker == "HPG"
    assert result.signals[0].signal_type == "strong_move"


@pytest.mark.asyncio
async def test_scan_negative_strong_move_included(mock_repo, mock_quote_service):
    """Ticker with <=-5% move also qualifies as strong_move."""
    item = make_item(ticker="VCB")
    mock_repo.list_for_user.return_value = [item]
    mock_quote_service.get_quote.return_value = _make_quote(price=50000.0, change_pct=-5.5)
    svc = _make_service(mock_repo, mock_quote_service)

    result = await svc.scan_user(user_id="user-A")
    assert len(result.signals) == 1
    assert result.signals[0].signal_type == "strong_move"


# ---------------------------------------------------------------------------
# Alert triggering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_triggers_price_above_alert(mock_repo, mock_quote_service):
    """PRICE_ABOVE alert fires when current price >= threshold."""
    alert = make_alert(
        ticker="HPG",
        condition_type=AlertConditionType.PRICE_ABOVE,
        threshold=28000.0,
        status=AlertStatus.ACTIVE,
    )
    item = make_item(ticker="HPG")
    item.alerts = [alert]
    mock_repo.list_for_user.return_value = [item]
    mock_quote_service.get_quote.return_value = _make_quote(price=29000.0, change_pct=1.0)
    svc = _make_service(mock_repo, mock_quote_service)

    result = await svc.scan_user(user_id="user-A")

    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.has_alerts is True
    assert signal.signal_type == "alert_triggered"
    assert len(signal.triggered_alerts) == 1
    assert alert.status == AlertStatus.TRIGGERED


@pytest.mark.asyncio
async def test_scan_does_not_trigger_inactive_alert(mock_repo, mock_quote_service):
    """Already-triggered alert must not fire again."""
    alert = make_alert(
        ticker="HPG",
        condition_type=AlertConditionType.PRICE_ABOVE,
        threshold=28000.0,
        status=AlertStatus.TRIGGERED,  # already triggered
    )
    item = make_item(ticker="HPG")
    item.alerts = [alert]
    mock_repo.list_for_user.return_value = [item]
    mock_quote_service.get_quote.return_value = _make_quote(price=35000.0, change_pct=1.0)
    svc = _make_service(mock_repo, mock_quote_service)

    result = await svc.scan_user(user_id="user-A")
    # No alert triggered, price move < 5% → no signal
    assert result.triggered_count == 0


# ---------------------------------------------------------------------------
# Error isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_ticker_error_captured_not_raised(mock_repo, mock_quote_service):
    """Quote failure for one ticker must not abort the whole scan."""
    items = [make_item("HPG"), make_item("ERR", item_id=2)]
    mock_repo.list_for_user.return_value = items

    def get_quote_side_effect(ticker: str):
        if ticker == "ERR":
            raise RuntimeError("adapter error")
        return _make_quote(price=29000.0, change_pct=6.0)  # strong move for HPG

    mock_quote_service.get_quote.side_effect = get_quote_side_effect
    svc = _make_service(mock_repo, mock_quote_service)

    result = await svc.scan_user(user_id="user-A")

    assert "ERR" in result.errors
    assert "HPG" not in result.errors
    assert len(result.signals) == 1  # HPG still processed


# ---------------------------------------------------------------------------
# ScanResult aggregation
# ---------------------------------------------------------------------------


def test_scan_result_triggered_count():
    alert1 = make_alert(alert_id=1)
    alert1.status = AlertStatus.TRIGGERED
    signal1 = ScanSignal(ticker="HPG", current_price=29000.0, change_pct=2.0)
    signal1.triggered_alerts = [alert1]

    signal2 = ScanSignal(ticker="VCB", current_price=50000.0, change_pct=0.5)

    from datetime import datetime

    result = ScanResult(scanned_at=datetime.utcnow(), signals=[signal1, signal2])
    assert result.triggered_count == 1
    assert result.tickers_with_signals == ["HPG"]


def test_scan_result_triggered_alerts_flat():
    alert1 = make_alert(alert_id=1)
    alert2 = make_alert(alert_id=2)
    s1 = ScanSignal(ticker="HPG", current_price=1.0, change_pct=0.0)
    s1.triggered_alerts = [alert1, alert2]
    s2 = ScanSignal(ticker="VCB", current_price=1.0, change_pct=0.0)

    from datetime import datetime

    result = ScanResult(scanned_at=datetime.utcnow(), signals=[s1, s2])
    assert len(result.triggered_alerts) == 2


# ---------------------------------------------------------------------------
# ScanSignal helpers
# ---------------------------------------------------------------------------


def test_signal_description_with_alert():
    alert = make_alert()
    s = ScanSignal(ticker="HPG", current_price=29000.0, change_pct=2.0)
    s.triggered_alerts = [alert]
    assert "1" in s.description
    assert "alert" in s.description.lower()


def test_signal_description_without_alert():
    s = ScanSignal(ticker="HPG", current_price=29000.0, change_pct=3.5)
    assert "+3.5%" in s.description or "3.5" in s.description


# ---------------------------------------------------------------------------
# Wave B3: VOLUME_SPIKE dùng vol_ratio_20 thật từ market.TickerContext
# ---------------------------------------------------------------------------


def _make_ctx(
    ticker: str, price: float, change_pct: float, vol_ratio_20: float | None
) -> MagicMock:
    ctx = MagicMock()
    ctx.ticker = ticker
    ctx.quote = _make_quote(price=price, change_pct=change_pct)
    ctx.quote.ticker = ticker
    del ctx.quote.volume_ratio  # Quote thật không có volume_ratio
    ctx.vol_ratio_20 = vol_ratio_20
    return ctx


def _volume_alert(threshold: float = 2.0):
    alert = make_alert(
        ticker="HPG", condition_type=AlertConditionType.VOLUME_SPIKE, threshold=threshold
    )
    item = make_item(ticker="HPG")
    item.alerts = [alert]
    return item, alert


@pytest.mark.asyncio
async def test_volume_spike_fires_with_ticker_context(mock_repo, mock_quote_service):
    item, alert = _volume_alert(threshold=2.0)
    mock_repo.list_for_user.return_value = [item]
    tcs = AsyncMock()
    tcs.get_many.return_value = {"HPG": _make_ctx("HPG", 29000.0, 0.5, vol_ratio_20=2.7)}
    svc = _make_service(mock_repo, mock_quote_service)
    svc._ticker_context_service = tcs

    result = await svc.scan_user(user_id="user-A")

    tcs.get_many.assert_awaited_once_with(["HPG"])
    mock_quote_service.get_bulk_quotes.assert_not_awaited()  # không fetch quote 2 lần
    assert len(result.signals) == 1 and result.signals[0].triggered_alerts == [alert]
    assert result.signals[0]._volume_ratio == pytest.approx(2.7)
    assert alert.status == AlertStatus.TRIGGERED


@pytest.mark.asyncio
async def test_volume_spike_silent_without_ticker_context(mock_repo, mock_quote_service):
    """Hành vi cũ giữ nguyên: không có context → ratio 1.0 → không trigger."""
    item, alert = _volume_alert(threshold=2.0)
    mock_repo.list_for_user.return_value = [item]
    q = _make_quote(price=29000.0, change_pct=0.5)
    del q.volume_ratio
    mock_quote_service.get_quote.return_value = q
    svc = _make_service(mock_repo, mock_quote_service)

    result = await svc.scan_user(user_id="user-A")

    assert result.signals == [] and alert.status == AlertStatus.ACTIVE


@pytest.mark.asyncio
async def test_ticker_context_failure_falls_back_to_bulk_quotes(mock_repo, mock_quote_service):
    item = make_item(ticker="HPG")
    item.alerts = [make_alert(ticker="HPG", threshold=28000.0)]
    mock_repo.list_for_user.return_value = [item]
    q = _make_quote(price=29000.0, change_pct=1.0)
    q.ticker = "HPG"
    mock_quote_service.get_bulk_quotes.return_value = [q]
    tcs = AsyncMock()
    tcs.get_many.side_effect = RuntimeError("ohlcv down")
    svc = _make_service(mock_repo, mock_quote_service)
    svc._ticker_context_service = tcs

    result = await svc.scan_user(user_id="user-A")

    mock_quote_service.get_bulk_quotes.assert_awaited_once()
    assert len(result.signals) == 1 and result.signals[0].has_alerts


def test_resolve_volume_ratio_precedence():
    from src.watchlist.scan_service import _resolve_volume_ratio

    q = MagicMock()
    q.volume_ratio = 1.8
    ctx = MagicMock()
    ctx.vol_ratio_20 = 3.2
    assert _resolve_volume_ratio(q, ctx) == 3.2  # context thắng
    ctx.vol_ratio_20 = None
    assert _resolve_volume_ratio(q, ctx) == 1.8  # fallback legacy attr
    del q.volume_ratio
    assert _resolve_volume_ratio(q, None) == 1.0  # trung tính
    ctx.vol_ratio_20 = 0.0
    assert _resolve_volume_ratio(q, ctx) == 1.0  # ratio 0 không hợp lệ
