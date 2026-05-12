"""AlertService — owns the full lifecycle of Alert entities.

Owner: watchlist segment.

Responsibilities:
  - Create alerts attached to watchlist items
  - Create thesis-trigger alert rules (from StressTestSubscriber)
  - List alerts for a user (all or active-only)
  - Process a batch of triggered alerts (called by ScanService)
  - Dismiss an alert by ID
  - Reactivate a TRIGGERED alert back to ACTIVE
  - Bulk-reactivate cooled-down alerts (called by WatchlistScanScheduler)

ScanService detects WHICH alerts are triggered; AlertService decides
what happens WHEN they are triggered (state mutation, persistence).

Does NOT fire Discord notifications — that is a bot/adapter concern.
Callers receive the list of fired Alert objects and dispatch as needed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.watchlist.models import Alert, AlertConditionType, AlertStatus
from src.watchlist.repository import WatchlistRepository

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Condition-aware cooldown config for bulk reactivation.
#
# Only types listed here are ever auto-reactivated by reactivate_cooled_down().
# Types NOT listed (PRICE_ABOVE, PRICE_BELOW, THESIS_TRIGGER) are one-shot
# and must be reactivated manually via reactivate() or dismissed.
#
# Rationale:
#   CHANGE_PCT_UP / CHANGE_PCT_DOWN — threshold (≥3%) filters intra-session
#     noise well enough; 4h cooldown prevents double-fire within same session.
#   VOLUME_SPIKE — volume surge can persist all session; 24h cooldown ensures
#     at most one alert per trading day.
#   PRICE_ABOVE / PRICE_BELOW — price can oscillate around threshold repeatedly;
#     re-arming automatically creates high noise. Investor must decide.
#   THESIS_TRIGGER — narrative watch rule from AI stress-test; never
#     auto-triggered or auto-reactivated (investor action required).
# ---------------------------------------------------------------------------

_REACTIVATE_COOLDOWN_HOURS: dict[AlertConditionType, int] = {
    AlertConditionType.CHANGE_PCT_UP:   4,
    AlertConditionType.CHANGE_PCT_DOWN: 4,
    AlertConditionType.VOLUME_SPIKE:    24,
}


class AlertNotFoundError(Exception):
    """Raised when an alert cannot be found or does not belong to the user."""


class AlertService:
    """Manages Alert lifecycle within the watchlist segment.

    Owner: watchlist segment.
    Caller: ScanService (process_triggered), WatchlistService (create/dismiss),
            StressTestSubscriber (create_thesis_trigger_rule),
            bot/api adapters (list, reactivate, reactivate_cooled_down).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = WatchlistRepository(session)

    # ------------------------------------------------------------------
    # Create — standard price/condition alerts
    # ------------------------------------------------------------------

    async def create(
        self,
        user_id: str,
        ticker: str,
        watchlist_item_id: int | None,
        condition_type: AlertConditionType,
        threshold: float,
        note: str | None = None,
    ) -> Alert:
        """Create and persist a new ACTIVE alert.

        Args:
            user_id: Owner of the alert.
            ticker: Stock symbol (will be uppercased).
            watchlist_item_id: FK to WatchlistItem, or None for standalone alerts.
            condition_type: Trigger condition enum value.
            threshold: Numeric threshold for the condition.
            note: Optional free-text note.

        Returns:
            Persisted Alert instance in ACTIVE state.
        """
        alert = Alert(
            user_id=user_id,
            ticker=ticker.upper(),
            watchlist_item_id=watchlist_item_id,
            condition_type=condition_type,
            threshold=threshold,
            status=AlertStatus.ACTIVE,
            note=note,
        )
        await self._repo.save_alert(alert)
        logger.info(
            "alert_service.created",
            user_id=user_id,
            ticker=ticker,
            condition=condition_type,
            threshold=threshold,
        )
        return alert

    # ------------------------------------------------------------------
    # Create — thesis trigger rules (Wave 2, from StressTestSubscriber)
    # ------------------------------------------------------------------

    async def rule_exists_by_dedup_key(
        self,
        user_id: str,
        dedup_key: str,
    ) -> bool:
        """Check if an alert rule with this dedup_key already exists for user.

        Used by StressTestSubscriber to prevent duplicate rule creation
        when the same stress-test result is processed more than once.

        Args:
            user_id:   Owner of the alert.
            dedup_key: Unique key, e.g. "stress:{thesis_id}:{trigger_index}".

        Returns:
            True if a matching rule already exists.
        """
        result = await self._session.execute(
            select(Alert).where(
                Alert.user_id == user_id,
                Alert.dedup_key == dedup_key,
            )
        )
        return result.scalar_one_or_none() is not None

    async def create_thesis_trigger_rule(
        self,
        user_id: str,
        symbol: str,
        label: str,
        trigger_description: str,
        thesis_id: str,
        dedup_key: str,
        source_event_id: str,
        invalidation_probability: float,
    ) -> Alert:
        """Create a watch alert rule from a thesis stress-test trigger.

        Rule type: THESIS_TRIGGER.
        Priority is derived from invalidation_probability:
            >= 0.7  → HIGH
            >= 0.4  → MEDIUM
            < 0.4   → LOW

        Does NOT commit — caller (StressTestSubscriber) commits once
        after all rules for the event are created.

        Args:
            user_id:                  Owner of the rule.
            symbol:                   Ticker symbol (uppercased internally).
            label:                    Human-readable label shown in Discord/UI.
            trigger_description:      Full AI-generated trigger text.
            thesis_id:                ID of the source thesis (str).
            dedup_key:                Dedup guard, e.g. "stress:{thesis_id}:{idx}".
            source_event_id:          event_id of StressTestCompletedEvent for tracing.
            invalidation_probability: Float 0-1 from AI result.

        Returns:
            Alert instance added to session (not yet committed).
        """
        priority = (
            "HIGH"   if invalidation_probability >= 0.7 else
            "MEDIUM" if invalidation_probability >= 0.4 else
            "LOW"
        )
        alert = Alert(
            user_id=user_id,
            ticker=symbol.upper(),
            condition_type=AlertConditionType.THESIS_TRIGGER,
            threshold=0.0,           # N/A for narrative triggers
            status=AlertStatus.ACTIVE,
            note=trigger_description,
            label=label,
            thesis_id=thesis_id,
            dedup_key=dedup_key,
            source_event_id=source_event_id,
            priority=priority,
        )
        self._session.add(alert)
        logger.info(
            "alert_service.thesis_trigger_rule_created",
            user_id=user_id,
            symbol=symbol,
            thesis_id=thesis_id,
            priority=priority,
            dedup_key=dedup_key,
        )
        return alert

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_for_user(self, user_id: str) -> list[Alert]:
        """Return all active alerts for a user, newest first."""
        return await self._repo.list_active_alerts(user_id)

    # ------------------------------------------------------------------
    # Process triggered (called by ScanService)
    # ------------------------------------------------------------------

    async def process_triggered(
        self,
        alerts: list[Alert],
        price_map: dict[str, float],
    ) -> list[Alert]:
        """Transition a batch of alerts to TRIGGERED state and persist.

        Called by ScanService after it detects which alerts fired.
        AlertService is the single place that calls alert.mark_triggered().

        Args:
            alerts: Alerts that ScanService determined should fire.
            price_map: Mapping of ticker -> current price for triggered_price field.

        Returns:
            The same list of alerts after mark_triggered() is called.
            Caller (bot adapter) uses this list to build Discord notifications.
        """
        fired: list[Alert] = []
        for alert in alerts:
            price = price_map.get(alert.ticker)
            alert.mark_triggered(price=price)
            self._session.add(alert)
            fired.append(alert)

        if fired:
            await self._session.flush()
            logger.info(
                "alert_service.process_triggered",
                count=len(fired),
                tickers=sorted({a.ticker for a in fired}),
            )
        return fired

    # ------------------------------------------------------------------
    # Dismiss
    # ------------------------------------------------------------------

    async def dismiss(self, alert_id: int, user_id: str) -> Alert:
        """Dismiss an alert by ID, scoped to the requesting user.

        Args:
            alert_id: Primary key of the alert.
            user_id: Must match alert.user_id (ownership check).

        Returns:
            Alert in DISMISSED state.

        Raises:
            AlertNotFoundError: If alert does not exist or belongs to another user.
        """
        alert = await self._get_owned(alert_id, user_id)
        alert.status = AlertStatus.DISMISSED
        self._session.add(alert)
        await self._session.flush()
        logger.info("alert_service.dismissed", alert_id=alert_id, user_id=user_id)
        return alert

    # ------------------------------------------------------------------
    # Reactivate (single alert — manual, called from bot/api adapters)
    # ------------------------------------------------------------------

    async def reactivate(self, alert_id: int, user_id: str) -> Alert:
        """Reset a TRIGGERED alert back to ACTIVE so it can fire again.

        Args:
            alert_id: Primary key of the alert.
            user_id: Must match alert.user_id (ownership check).

        Returns:
            Alert in ACTIVE state.

        Raises:
            AlertNotFoundError: If alert does not exist or belongs to another user.
        """
        alert = await self._get_owned(alert_id, user_id)

        if alert.status == AlertStatus.ACTIVE:
            return alert

        if alert.status == AlertStatus.DISMISSED:
            logger.warning(
                "alert_service.reactivate_dismissed",
                alert_id=alert_id,
                user_id=user_id,
            )

        alert.status = AlertStatus.ACTIVE
        alert.triggered_at = None
        alert.triggered_price = None
        self._session.add(alert)
        await self._session.flush()

        logger.info(
            "alert_service.reactivated",
            alert_id=alert_id,
            user_id=user_id,
            ticker=alert.ticker,
        )
        return alert

    # ------------------------------------------------------------------
    # Reactivate cooled-down (bulk — called by WatchlistScanScheduler)
    # ------------------------------------------------------------------

    async def reactivate_cooled_down(
        self,
        user_id: str,
        cooldown_hours: int = 4,  # kept for backward compat; ignored internally
    ) -> list[Alert]:
        """Reactivate TRIGGERED alerts that have passed their condition-specific cooldown.

        Uses _REACTIVATE_COOLDOWN_HOURS to determine which condition types are
        eligible and how long their cooldown window is. Types not in the mapping
        (PRICE_ABOVE, PRICE_BELOW, THESIS_TRIGGER) are never auto-reactivated.

        Called by WatchlistScanScheduler at the start of each scan tick,
        in an isolated session before ScanService.scan_user() runs.

        Args:
            user_id:        Owner of the alerts.
            cooldown_hours: Deprecated — kept for call-site backward compatibility.
                            Cooldown is now determined per condition type via
                            _REACTIVATE_COOLDOWN_HOURS. This parameter is ignored.

        Returns:
            List of Alert instances that were reset to ACTIVE this tick.
            Empty list if none were eligible.
        """
        now = datetime.now(UTC)
        reactivated: list[Alert] = []

        for condition_type, hours in _REACTIVATE_COOLDOWN_HOURS.items():
            cutoff = now - timedelta(hours=hours)
            stmt = select(Alert).where(
                and_(
                    Alert.user_id == user_id,
                    Alert.status == AlertStatus.TRIGGERED,
                    Alert.condition_type == condition_type,
                    Alert.triggered_at < cutoff,
                )
            )
            result = await self._session.execute(stmt)
            alerts = result.scalars().all()

            for alert in alerts:
                alert.status = AlertStatus.ACTIVE
                alert.triggered_at = None
                alert.triggered_price = None
                self._session.add(alert)

            reactivated.extend(alerts)

        if reactivated:
            await self._session.flush()
            logger.info(
                "alert_service.bulk_reactivated",
                user_id=user_id,
                count=len(reactivated),
                tickers=sorted({a.ticker for a in reactivated}),
                by_type={
                    ct.value: sum(1 for a in reactivated if a.condition_type == ct)
                    for ct in _REACTIVATE_COOLDOWN_HOURS
                    if any(a.condition_type == ct for a in reactivated)
                },
            )

        return reactivated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_owned(self, alert_id: int, user_id: str) -> Alert:
        """Fetch an alert by PK and verify ownership. Raises AlertNotFoundError if missing."""
        stmt = select(Alert).where(Alert.id == alert_id).where(Alert.user_id == user_id)
        result = await self._session.execute(stmt)
        alert = result.scalar_one_or_none()
        if alert is None:
            raise AlertNotFoundError(f"Alert {alert_id} not found for user {user_id}")
        return alert
