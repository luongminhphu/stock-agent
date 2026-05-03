"""AlertService — owns the full lifecycle of Alert entities.

Owner: watchlist segment.

Responsibilities:
  - Create alerts attached to watchlist items
  - List alerts for a user (all or active-only)
  - Process a batch of triggered alerts (called by ScanService)
  - Dismiss an alert by ID
  - Reactivate a TRIGGERED alert back to ACTIVE (manual or auto via model flag)

ScanService detects WHICH alerts are triggered; AlertService decides
what happens WHEN they are triggered (state mutation, persistence).

Does NOT fire Discord notifications — that is a bot/adapter concern.
Callers receive the list of fired Alert objects and dispatch as needed.

Recurring alerts:
  Set alert.auto_reactivate=True at creation time. mark_triggered() will
  record triggered_at/triggered_price but leave status=ACTIVE so the next
  scan can fire again without manual intervention.
  For one-shot alerts (default), use reactivate() to manually reset.
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
        auto_reactivate: bool = False,
    ) -> Alert:
        """Create and persist a new ACTIVE alert.

        Args:
            user_id: Owner of the alert.
            ticker: Stock symbol (will be uppercased).
            watchlist_item_id: FK to WatchlistItem, or None for standalone alerts.
            condition_type: Trigger condition enum value.
            threshold: Numeric threshold for the condition.
            note: Optional free-text note.
            auto_reactivate: If True, alert stays ACTIVE after firing (recurring).
                             If False (default), alert moves to TRIGGERED after firing.

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
            auto_reactivate=auto_reactivate,
        )
        await self._repo.save_alert(alert)
        logger.info(
            "alert_service.created",
            user_id=user_id,
            ticker=ticker,
            condition=condition_type,
            threshold=threshold,
            auto_reactivate=auto_reactivate,
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

        For auto_reactivate alerts, mark_triggered() keeps status=ACTIVE
        (handled inside the model method) — they will be eligible to fire
        again on the next scan tick.

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
                auto_reactivate_count=sum(1 for a in fired if a.auto_reactivate),
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

        Use this for one-shot alerts (auto_reactivate=False) when the investor
        wants to re-arm the same alert without deleting and recreating it.
        Alerts with auto_reactivate=True never reach TRIGGERED, so calling
        this on them is a no-op that returns the alert unchanged.

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
            # Already active — idempotent, nothing to do
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
