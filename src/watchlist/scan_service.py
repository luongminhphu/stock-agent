"""Scan service — evaluates watchlist items against live quotes.

Owner: watchlist segment.
Consumes QuoteService (market segment) via injection.

Responsibility boundary:
  ScanService     → detect which alerts are triggered, build ScanSignal/ScanResult
  AlertService    → mutate alert state (mark_triggered), persist fired alerts
  ReminderService → owns cooldown logic for ON_SIGNAL reminders

ScanService calls AlertService.process_triggered() after collecting all
triggered alerts for a ticker. It does NOT call alert.mark_triggered() directly.

For ON_SIGNAL reminders, ScanService calls:
    ReminderService.list_due_for_signal(tickers)
    ReminderService.mark_sent(reminder)
and returns the fired reminders as part of ScanResult.on_signal_reminders so
the bot adapter (WatchlistScanScheduler) can dispatch Discord notifications.
ScanService itself NEVER sends Discord messages.

Note: _persist_snapshot does NOT commit — caller is responsible for session lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.watchlist.alert_service import AlertService
from src.watchlist.models import Alert, AlertStatus, Reminder, WatchlistScan
from src.watchlist.reminder_service import ReminderService
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
    # ON_SIGNAL reminders that fired this tick — bot adapter dispatches Discord
    on_signal_reminders: list[Reminder] = field(default_factory=list)

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
    """Scan all watchlist tickers and evaluate alert conditions.

    Detects signals only — delegates alert state mutation to AlertService
    and reminder cooldown logic to ReminderService.
    """

    def __init__(
        self,
        session: AsyncSession,
        quote_service: object | None = None,
    ) -> None:
        self._session = session
        self._repo = WatchlistRepository(session)
        self._alert_service = AlertService(session)
        self._reminder_service = ReminderService(session)
        self._quote_service = quote_service

    async def scan_user(self, user_id: str) -> ScanResult:
        if self._quote_service is None:
            raise ScanServiceNotConfiguredError("ScanService requires a QuoteService adapter.")

        items = await self._repo.list_for_user(user_id)
        tickers = sorted({i.ticker for i in items})
        result = ScanResult(scanned_at=datetime.now(UTC))

        # price_map used by AlertService.process_triggered for triggered_price field
        price_map: dict[str, float] = {}

        for ticker in tickers:
            try:
                signal = await self._scan_ticker(ticker, items)
                price_map[ticker] = signal.current_price
                if signal.has_alerts or abs(signal.change_pct) >= 3:
                    result.signals.append(signal)
            except Exception as exc:
                logger.warning("scan.ticker_error", ticker=ticker, error=str(exc))
                result.errors[ticker] = str(exc)

        # Delegate alert state mutation to AlertService
        all_triggered = result.triggered_alerts
        if all_triggered:
            await self._alert_service.process_triggered(all_triggered, price_map)

        # Fire ON_SIGNAL reminders for tickers that had any signal this tick
        # ReminderService.list_due_for_signal() owns the 1h cooldown logic
        if result.signals:
            signal_tickers = [s.ticker for s in result.signals]
            result.on_signal_reminders = await self._fire_on_signal_reminders(signal_tickers)

        logger.info(
            "scan.complete",
            user_id=user_id,
            scanned=len(tickers),
            triggered=result.triggered_count,
            on_signal_reminders=len(result.on_signal_reminders),
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
        """Fetch quote and detect triggered alerts. Does NOT mutate alert state."""
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
                # Append only — AlertService.process_triggered() handles mark_triggered
                signal.triggered_alerts.append(alert)

        return signal

    async def _fire_on_signal_reminders(self, tickers: list[str]) -> list[Reminder]:
        """Query and mark ON_SIGNAL reminders due for the given tickers.

        Delegates cooldown logic entirely to ReminderService — ScanService
        has no knowledge of frequency deltas.

        Returns the list of fired Reminder objects so WatchlistScanScheduler
        can include them in the Discord notification embed.

        Failures are swallowed so a reminder error never blocks scan delivery.
        """
        try:
            due = await self._reminder_service.list_due_for_signal(tickers)
            for reminder in due:
                try:
                    await self._reminder_service.mark_sent(reminder)
                except Exception as exc:
                    logger.error(
                        "scan.on_signal_reminder_mark_sent_failed",
                        reminder_id=reminder.id,
                        error=str(exc),
                    )
            if due:
                logger.info(
                    "scan.on_signal_reminders_fired",
                    count=len(due),
                    tickers=tickers,
                )
            return due
        except Exception as exc:
            logger.error("scan.on_signal_reminders_error", tickers=tickers, error=str(exc))
            return []

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
