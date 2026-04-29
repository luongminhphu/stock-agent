"""Scan service — evaluates watchlist items against live quotes.

Owner: watchlist segment.
Consumes QuoteService (market segment) via injection.
Does NOT own alert-firing logic — calls alert.is_triggered_by() (domain helper)
then returns structured scan result for bot/api adapters.

Note: _persist_snapshot does NOT commit — caller is responsible for session lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

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
        if abs(self.change_pct) >= 3:
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
                if signal.has_alerts or abs(signal.change_pct) >= 3:
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

    async def get_latest_snapshot(self, user_id: str) -> WatchlistScan | None:
        return await self._repo.get_latest_scan(user_id)

    async def scan_user_if_stale(
        self, user_id: str, max_age_minutes: int = 30
    ) -> WatchlistScan | None:
        latest = await self.get_latest_snapshot(user_id)
        now = datetime.now(UTC)

        if latest is not None and latest.scanned_at is not None:
            if (now - latest.scanned_at) < timedelta(minutes=max_age_minutes):
                logger.info(
                    "scan.reuse_latest_snapshot",
                    user_id=user_id,
                    snapshot_id=latest.id,
                    scanned_at=latest.scanned_at.isoformat(),
                )
                return latest

        await self.scan_user(user_id)
        return await self.get_latest_snapshot(user_id)

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
                alert.mark_triggered(price=quote.price)

        return signal

    async def _persist_snapshot(self, user_id: str, result: ScanResult) -> None:
        """Stage a WatchlistScan snapshot — caller must commit the session.

        Failures are logged and swallowed so a DB error never blocks
        scan result delivery to the caller.
        """
        try:
            snapshot = WatchlistScan(
                user_id=user_id,
                summary=result.build_summary(),
                scanned_at=result.scanned_at,
            )
            self._session.add(snapshot)
            logger.info("scan.snapshot_staged", user_id=user_id)
        except Exception as exc:
            logger.error("scan.snapshot_stage_failed", user_id=user_id, error=str(exc))


class ScanServiceNotConfiguredError(Exception):
    """Raised when ScanService is called without a QuoteService adapter."""
