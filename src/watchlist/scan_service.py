"""Scan service — evaluates watchlist items against live quotes.

Owner: watchlist segment.
Consumes QuoteService (market segment) via injection.
Does NOT own alert-firing logic — calls alert.is_triggered_by() (domain helper)
then returns structured scan result for bot/api adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.watchlist.models import Alert, AlertStatus, WatchlistScan
from src.watchlist.repository import WatchlistRepository

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

    @property
    def signal_type(self) -> str:
        if self.triggered_alerts:
            return "alert_triggered"
        if abs(self.change_pct) >= 5:
            return "strong_move"
        return "watch"

    @property
    def description(self) -> str:
        if self.triggered_alerts:
            return f"{len(self.triggered_alerts)} alert(s) triggered"
        return f"Price move {self.change_pct:+.1f}%"


@dataclass
class ScanResult:
    """Aggregated result of a full watchlist scan."""

    scanned_at: datetime
    signals: list[ScanSignal] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def triggered_count(self) -> int:
        return sum(1 for s in self.signals if s.has_alerts)

    @property
    def tickers_with_signals(self) -> list[str]:
        return [s.ticker for s in self.signals if s.has_alerts]

    @property
    def triggered_alerts(self) -> list[Alert]:
        alerts: list[Alert] = []
        for signal in self.signals:
            alerts.extend(signal.triggered_alerts)
        return alerts

    def build_summary(self) -> str:
        """Build a human-readable summary string for persisting to WatchlistScan."""
        if not self.signals:
            return "Không có tín hiệu đáng chú ý."
        parts = []
        for s in self.signals:
            parts.append(f"{s.ticker}: {s.description} ({s.change_pct:+.1f}%)")
        if self.errors:
            parts.append(f"Lỗi fetch: {', '.join(self.errors.keys())}")
        return "; ".join(parts)


class ScanService:
    """Scan all watchlist tickers and evaluate alert conditions."""

    def __init__(
        self,
        session: AsyncSession,
        quote_service: object | None = None,
    ) -> None:
        self._session = session
        self._repo = WatchlistRepository(session)
        self._quote_service = quote_service

    async def scan_user(self, user_id: str) -> ScanResult:
        if self._quote_service is None:
            raise ScanServiceNotConfiguredError("ScanService requires a QuoteService adapter.")

        items = await self._repo.list_for_user(user_id)
        tickers = sorted({i.ticker for i in items})
        result = ScanResult(scanned_at=datetime.now(UTC))

        for ticker in tickers:
            try:
                signal = await self._scan_ticker(ticker, items)
                if signal.has_alerts or abs(signal.change_pct) >= 5:
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

        await self._persist_snapshot(user_id, result)
        return result

    async def scan_for_user(self, user_id: str) -> ScanResult:
        return await self.scan_user(user_id)

    async def _scan_ticker(self, ticker: str, items: list) -> ScanSignal:
        quote = await self._quote_service.get_quote(ticker)  # type: ignore[union-attr]
        volume_ratio = 1.0

        signal = ScanSignal(
            ticker=ticker,
            current_price=quote.price,
            change_pct=quote.change_pct,
        )

        all_alerts: list[Alert] = []
        for item in items:
            if item.ticker == ticker:
                all_alerts.extend(a for a in item.alerts if a.status == AlertStatus.ACTIVE)

        for alert in all_alerts:
            if alert.is_triggered_by(
                current_price=quote.price,
                change_pct=quote.change_pct,
                volume_ratio=volume_ratio,
            ):
                signal.triggered_alerts.append(alert)
                alert.mark_triggered()

        return signal

    async def _persist_snapshot(self, user_id: str, result: ScanResult) -> None:
        """Persist a WatchlistScan snapshot so the dashboard can display it.

        Failures are logged and swallowed — a DB error must never block
        scan result delivery to the caller.
        """
        try:
            snapshot = WatchlistScan(
                user_id=user_id,
                summary=result.build_summary(),
                scanned_at=result.scanned_at,
            )
            self._session.add(snapshot)
            await self._session.commit()
            logger.info("scan.snapshot_saved", user_id=user_id, snapshot_id=snapshot.id)
        except Exception as exc:
            logger.error("scan.snapshot_save_failed", user_id=user_id, error=str(exc))
            await self._session.rollback()


class ScanServiceNotConfiguredError(Exception):
    """Raised when ScanService is called without a QuoteService adapter."""
