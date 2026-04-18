"""Scan service — evaluates watchlist items against live quotes.

Owner: watchlist segment.
Consumes QuoteService (market segment) via injection.
Does NOT own alert-firing logic — calls alert.is_triggered_by() (domain helper)
then delegates to WatchlistService to update state.

Wave 2: integrate with real QuoteService adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.watchlist.models import Alert, AlertStatus
from src.watchlist.repository import WatchlistRepository
from src.platform.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ScanSignal:
    """A signal produced by ScanService for a single ticker."""
    ticker: str
    current_price: float
    change_pct: float
    triggered_alerts: list[Alert] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_alerts(self) -> bool:
        return len(self.triggered_alerts) > 0


@dataclass
class ScanResult:
    """Aggregated result of a full watchlist scan."""
    scanned_at: datetime
    signals: list[ScanSignal] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # ticker -> error message

    @property
    def triggered_count(self) -> int:
        return sum(1 for s in self.signals if s.has_alerts)

    @property
    def tickers_with_signals(self) -> list[str]:
        return [s.ticker for s in self.signals if s.has_alerts]


class ScanService:
    """Scan all watchlist tickers and evaluate alert conditions.

    Injected dependencies:
        session       — AsyncSession for DB access
        quote_service — QuoteService (market segment), optional in Wave 1

    Wave 1: calling scan() raises ScanServiceNotConfiguredError.
    Wave 2: inject QuoteService adapter, implement _fetch_quote().
    """

    def __init__(
        self,
        session: AsyncSession,
        quote_service: object | None = None,  # QuoteService — typed loosely to avoid circular
    ) -> None:
        self._repo = WatchlistRepository(session)
        self._quote_service = quote_service

    async def scan_user(self, user_id: str) -> ScanResult:
        """Scan all watchlist items for a single user."""
        if self._quote_service is None:
            raise ScanServiceNotConfiguredError(
                "ScanService requires a QuoteService adapter. Wire one in Wave 2."
            )

        items = await self._repo.list_for_user(user_id)
        tickers = [i.ticker for i in items]
        result = ScanResult(scanned_at=datetime.utcnow())

        for ticker in tickers:
            try:
                signal = await self._scan_ticker(ticker, items)
                result.signals.append(signal)
            except Exception as exc:
                logger.warning("scan.ticker_error", ticker=ticker, error=str(exc))
                result.errors[ticker] = str(exc)

        logger.info(
            "scan.complete",
            user_id=user_id,
            scanned=len(tickers),
            triggered=result.triggered_count,
        )
        return result

    async def _scan_ticker(
        self, ticker: str, items: list
    ) -> ScanSignal:
        quote = await self._quote_service.get_quote(ticker)  # type: ignore[union-attr]

        # Approximate volume ratio — Wave 2 will use 20-day avg volume
        volume_ratio = 1.0

        signal = ScanSignal(
            ticker=ticker,
            current_price=quote.price,
            change_pct=quote.change_pct,
        )

        # Collect all active alerts for this ticker across items
        all_alerts: list[Alert] = []
        for item in items:
            if item.ticker == ticker:
                all_alerts.extend(
                    a for a in item.alerts if a.status == AlertStatus.ACTIVE
                )

        for alert in all_alerts:
            if alert.is_triggered_by(
                current_price=quote.price,
                change_pct=quote.change_pct,
                volume_ratio=volume_ratio,
            ):
                signal.triggered_alerts.append(alert)

        return signal


class ScanServiceNotConfiguredError(Exception):
    """Raised when ScanService is called without a QuoteService adapter."""
