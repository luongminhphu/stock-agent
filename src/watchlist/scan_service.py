"""Scan service — evaluates watchlist items against live quotes.

Owner: watchlist segment.
Consumes QuoteService (market segment) via injection.
Consumes SignalCredibilityAgent (ai segment) via optional injection.

Responsibility boundary:
  ScanService            → detect which alerts are triggered, build ScanSignal/ScanResult
  AlertService           → mutate alert state (mark_triggered), persist fired alerts
  ReminderService        → owns cooldown logic for ON_SIGNAL reminders
  SignalCredibilityAgent → score signal credibility (optional enrichment, graceful degrade)

ScanService calls AlertService.process_triggered() after collecting all
triggered alerts for a ticker. It does NOT call alert.mark_triggered() directly.

For ON_SIGNAL reminders, ScanService calls:
    ReminderService.list_due_for_signal(tickers)
    ReminderService.mark_sent(reminder)
and returns the fired reminders as part of ScanResult.on_signal_reminders so
the bot adapter (WatchlistScanScheduler) can dispatch Discord notifications.
ScanService itself NEVER sends Discord messages.

Note: _persist_snapshot does NOT commit — caller (WatchlistScanScheduler)
is responsible for committing the session after scan_user() returns.
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
    # Optional credibility enrichment — None if agent unavailable or evaluation failed
    credibility: object | None = field(default=None, repr=False)

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
            base = f"{len(self.triggered_alerts)} alert(s) triggered"
        else:
            base = f"Price move {self.change_pct:+.1f}%"
        if self.credibility is not None:
            base += f" | {self.credibility.short_summary()}"
        return base


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
    Optionally enriches signals with credibility scores via SignalCredibilityAgent.
    """

    def __init__(
        self,
        session: AsyncSession,
        quote_service: object | None = None,
        credibility_agent: object | None = None,
    ) -> None:
        self._session = session
        self._repo = WatchlistRepository(session)
        self._alert_service = AlertService(session)
        self._reminder_service = ReminderService(session)
        self._quote_service = quote_service
        self._credibility_agent = credibility_agent

    async def scan_user(self, user_id: str) -> ScanResult:
        if self._quote_service is None:
            raise ScanServiceNotConfiguredError("ScanService requires a QuoteService adapter.")

        items = await self._repo.list_for_user(user_id)
        tickers = sorted({i.ticker for i in items})
        result = ScanResult(scanned_at=datetime.now(UTC))

        if not tickers:
            await self._persist_snapshot(user_id, result)
            return result

        # Bulk-fetch all quotes in a single call — avoids N serial round-trips.
        # Falls back to per-ticker get_quote when a ticker is missing from bulk result
        # (e.g. delisted symbol, adapter limitation).
        bulk_quote_map: dict[str, object] = {}
        try:
            bulk_quotes = await self._quote_service.get_bulk_quotes(tickers)  # type: ignore[union-attr]
            bulk_quote_map = {q.ticker: q for q in bulk_quotes}
        except Exception as exc:
            logger.warning("scan.bulk_quote_failed", tickers=tickers, error=str(exc))
            # bulk failed — per-ticker fallback handled inside loop

        # price_map used by AlertService.process_triggered for triggered_price field
        price_map: dict[str, float] = {}

        for ticker in tickers:
            try:
                signal = await self._scan_ticker(ticker, items, bulk_quote_map)
                price_map[ticker] = signal.current_price
                if signal.has_alerts or abs(signal.change_pct) >= 3:
                    # Enrich signal with credibility score (graceful degrade)
                    if self._credibility_agent is not None:
                        signal = await self._enrich_credibility(signal)
                    result.signals.append(signal)
            except Exception as exc:
                logger.warning("scan.ticker_error", ticker=ticker, error=str(exc))
                result.errors[ticker] = str(exc)

        # Delegate alert state mutation to AlertService
        all_triggered = result.triggered_alerts
        if all_triggered:
            await self._alert_service.process_triggered(all_triggered, price_map)

        # Fire ON_SIGNAL reminders for tickers that had any signal this tick
        if result.signals:
            signal_tickers = [s.ticker for s in result.signals]
            result.on_signal_reminders = await self._fire_on_signal_reminders(signal_tickers)

        logger.info(
            "scan.complete",
            user_id=user_id,
            scanned=len(tickers),
            triggered=result.triggered_count,
            credibility_enriched=sum(1 for s in result.signals if s.credibility is not None),
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

    async def _scan_ticker(
        self,
        ticker: str,
        items: list,
        bulk_quote_map: dict[str, object],
    ) -> ScanSignal:
        """Fetch quote (from bulk map or per-ticker fallback) and detect triggered alerts.

        Does NOT mutate alert state.
        """
        quote = bulk_quote_map.get(ticker)
        if quote is None:
            # Fallback: ticker missing from bulk result — try individual fetch
            logger.debug("scan.bulk_miss_fallback", ticker=ticker)
            quote = await self._quote_service.get_quote(ticker)  # type: ignore[union-attr]

        signal = ScanSignal(
            ticker=ticker,
            current_price=quote.price,  # type: ignore[union-attr]
            change_pct=quote.change_pct,  # type: ignore[union-attr]
        )

        all_alerts: list[Alert] = []
        for item in items:
            if item.ticker == ticker:
                all_alerts.extend(a for a in item.alerts if a.status == AlertStatus.ACTIVE)

        volume_ratio = getattr(quote, "volume_ratio", 1.0)

        for alert in all_alerts:
            if alert.is_triggered_by(
                current_price=quote.price,  # type: ignore[union-attr]
                change_pct=quote.change_pct,  # type: ignore[union-attr]
                volume_ratio=volume_ratio,
            ):
                signal.triggered_alerts.append(alert)

        return signal

    async def _enrich_credibility(self, signal: ScanSignal) -> ScanSignal:
        """Enrich a ScanSignal with credibility score. Failures are swallowed.

        Imports are local to avoid hard-coupling ai segment into watchlist at module level.
        The optional credibility_agent injection keeps the boundary clean.
        """
        try:
            from src.ai.agents.signal_credibility import SignalCredibilityResult  # noqa: F401
            from src.ai.prompts.signal_credibility import SignalCredibilityContext

            ctx = SignalCredibilityContext(
                ticker=signal.ticker,
                signal_type=signal.signal_type,
                current_price=signal.current_price,
                change_pct=signal.change_pct,
                volume_ratio=getattr(signal, "_volume_ratio", 1.0),
                price_5d_trend=0.0,   # TODO: inject from market.price_history once available
                recent_news="N/A",    # TODO: inject from market.news_service once available
                has_upcoming_earnings=False,
                alert_note=(
                    signal.triggered_alerts[0].note
                    if signal.triggered_alerts and hasattr(signal.triggered_alerts[0], "note")
                    else ""
                ),
            )
            result = await self._credibility_agent.evaluate(ctx)  # type: ignore[union-attr]
            signal.credibility = result
            if result:
                logger.debug(
                    "scan.credibility_enriched",
                    ticker=signal.ticker,
                    verdict=result.verdict,
                    score=result.score,
                )
        except Exception as exc:
            logger.warning(
                "scan.credibility_enrich_failed",
                ticker=signal.ticker,
                error=str(exc),
            )
        return signal

    async def _fire_on_signal_reminders(self, tickers: list[str]) -> list[Reminder]:
        """Query and mark ON_SIGNAL reminders due for the given tickers."""
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
        """Stage a WatchlistScan snapshot — caller (WatchlistScanScheduler) must commit."""
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
