"""AlertService — owns the full lifecycle of Alert entities.

Owner: watchlist segment.

Responsibilities:
  - Create alerts attached to watchlist items
  - List alerts for a user (all or active-only)
  - Process a batch of triggered alerts (called by ScanService)
  - Dismiss an alert by ID
  - Reactivate a TRIGGERED alert back to ACTIVE

ScanService detects WHICH alerts are triggered; AlertService decides
what happens WHEN they are triggered (state mutation, persistence).

Does NOT fire Discord notifications — that is a bot/adapter concern.
Callers receive the list of fired Alert objects and dispatch as needed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.watchlist.models import Alert, AlertConditionType, AlertStatus
from src.watchlist.repository import WatchlistRepository

logger = get_logger(__name__)


class AlertNotFoundError(Exception):
    """Raised when an alert cannot be found or does not belong to the user."""


class AlertService:
    """Manages Alert lifecycle within the watchlist segment.

    Owner: watchlist segment.
    Caller: ScanService (process_triggered), WatchlistService (create/dismiss),
            bot/api adapters (list, reactivate).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = WatchlistRepository(session)

    # ------------------------------------------------------------------
    # Create
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
    # Reactivate
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
