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
  - Record user reactions and adapt per-alert cooldown (Wave D)

ScanService detects WHICH alerts are triggered; AlertService decides
what happens WHEN they are triggered (state mutation, persistence).

Does NOT fire Discord notifications — that is a bot/adapter concern.
Callers receive the list of fired Alert objects and dispatch as needed.

Wave D — Adaptive cooldown loop:
  record_reaction(alert_id, user_id, reaction) is called by
  SignalReactionListener after each emoji reaction.
  Reaction outcomes:
    "acted" / "bought" / "sold"  → acknowledged, positive signal
    "watched" / "acknowledged"  → acknowledged, neutral
    "ignored" / "dismissed" / "flagged"  → dismiss_count++
  When dismiss_count reaches DISMISS_ESCALATION_THRESHOLD (3), the
  alert's effective_cooldown_hours is doubled (capped at
  MAX_EFFECTIVE_COOLDOWN_HOURS=48). This means if the investor
  repeatedly ignores the same alert type, the system backs off.
  reactivate_cooled_down() checks effective_cooldown_hours first;
  if None, falls back to the per-condition-type default.
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
# ---------------------------------------------------------------------------

_REACTIVATE_COOLDOWN_HOURS: dict[AlertConditionType, int] = {
    AlertConditionType.CHANGE_PCT_UP:   4,
    AlertConditionType.CHANGE_PCT_DOWN: 4,
    AlertConditionType.VOLUME_SPIKE:    24,
}

# ---------------------------------------------------------------------------
# Wave D: adaptive cooldown constants
# ---------------------------------------------------------------------------

# Number of dismiss-type reactions before cooldown is escalated.
DISMISS_ESCALATION_THRESHOLD: int = 3

# Absolute ceiling for effective_cooldown_hours (48h = 2 trading days).
MAX_EFFECTIVE_COOLDOWN_HOURS: int = 48

# Reactions that count as a "dismiss" for escalation purposes.
_DISMISS_REACTIONS: frozenset[str] = frozenset({"ignored", "dismissed", "flagged"})

# Reactions that count as positive/acknowledged (reset nothing, just increment reaction_count).
_POSITIVE_REACTIONS: frozenset[str] = frozenset({"acted", "bought", "sold", "watched", "acknowledged"})


class AlertNotFoundError(Exception):
    """Raised when an alert cannot be found or does not belong to the user."""


class AlertService:
    """Manages Alert lifecycle within the watchlist segment.

    Owner: watchlist segment.
    Caller: ScanService (process_triggered), WatchlistService (create/dismiss),
            StressTestSubscriber (create_thesis_trigger_rule),
            bot/api adapters (list, reactivate, reactivate_cooled_down),
            SignalReactionListener (record_reaction — Wave D).
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
        """Create and persist a new ACTIVE alert."""
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
        """Check if an alert rule with this dedup_key already exists for user."""
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
        """Create a watch alert rule from a thesis stress-test trigger."""
        priority = (
            "HIGH"   if invalidation_probability >= 0.7 else
            "MEDIUM" if invalidation_probability >= 0.4 else
            "LOW"
        )
        alert = Alert(
            user_id=user_id,
            ticker=symbol.upper(),
            condition_type=AlertConditionType.THESIS_TRIGGER,
            threshold=0.0,
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
        """Transition a batch of alerts to TRIGGERED state and persist."""
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
        """Dismiss an alert by ID, scoped to the requesting user."""
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
        """Reset a TRIGGERED alert back to ACTIVE so it can fire again."""
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
        cooldown_hours: int = 4,  # kept for backward compat; used as fallback only
    ) -> list[Alert]:
        """Reactivate TRIGGERED alerts that have passed their cooldown window.

        Wave D: respects effective_cooldown_hours when set on individual alerts.
        For each eligible condition type, alerts are split into two groups:
          1. Alerts with effective_cooldown_hours set — use that value.
          2. Alerts without (None) — use _REACTIVATE_COOLDOWN_HOURS default.

        Args:
            user_id:        Owner of the alerts.
            cooldown_hours: Deprecated — kept for call-site backward compatibility.
                            Ignored internally; each alert uses its own cooldown.

        Returns:
            List of Alert instances reset to ACTIVE this tick.
        """
        now = datetime.now(UTC)
        reactivated: list[Alert] = []

        for condition_type, default_hours in _REACTIVATE_COOLDOWN_HOURS.items():
            # Fetch all TRIGGERED alerts for this condition type.
            stmt = select(Alert).where(
                and_(
                    Alert.user_id == user_id,
                    Alert.status == AlertStatus.TRIGGERED,
                    Alert.condition_type == condition_type,
                )
            )
            result = await self._session.execute(stmt)
            candidates = result.scalars().all()

            for alert in candidates:
                # Wave D: use per-alert override if set, else condition-type default.
                hours = (
                    alert.effective_cooldown_hours
                    if alert.effective_cooldown_hours is not None
                    else default_hours
                )
                cutoff = now - timedelta(hours=hours)
                if alert.triggered_at is not None and alert.triggered_at < cutoff:
                    alert.status = AlertStatus.ACTIVE
                    alert.triggered_at = None
                    alert.triggered_price = None
                    self._session.add(alert)
                    reactivated.append(alert)

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
    # Wave D: record user reaction — adaptive cooldown feedback
    # ------------------------------------------------------------------

    async def record_reaction(
        self,
        alert_id: int,
        user_id: str,
        reaction: str,
    ) -> None:
        """Record a user reaction on an alert and adapt cooldown if needed.

        Wave D — feedback loop:
          - Every call increments reaction_count.
          - Dismiss-type reactions (ignored/dismissed/flagged) also increment
            dismiss_count.
          - When dismiss_count reaches DISMISS_ESCALATION_THRESHOLD:
              effective_cooldown_hours = min(
                  (current or default) * 2,
                  MAX_EFFECTIVE_COOLDOWN_HOURS
              )
            This causes reactivate_cooled_down() to wait longer before
            re-arming the alert, reducing noise for alerts the user ignores.

        Non-fatal: all errors are logged and swallowed so that a missing
        alert or DB error never interrupts the Discord event loop.

        Args:
            alert_id: PK of the alert that received the reaction.
            user_id:  Reactor — must match alert.user_id.
            reaction: Signal string from EMOJI_SIGNAL_MAP
                      (e.g. "bought", "sold", "watched", "ignored", "flagged").
        """
        try:
            stmt = select(Alert).where(
                Alert.id == alert_id,
                Alert.user_id == user_id,
            )
            result = await self._session.execute(stmt)
            alert = result.scalar_one_or_none()

            if alert is None:
                logger.debug(
                    "alert_service.record_reaction.alert_not_found",
                    alert_id=alert_id,
                    user_id=user_id,
                    reaction=reaction,
                )
                return

            alert.reaction_count = (alert.reaction_count or 0) + 1

            if reaction in _DISMISS_REACTIONS:
                alert.dismiss_count = (alert.dismiss_count or 0) + 1

                if alert.dismiss_count >= DISMISS_ESCALATION_THRESHOLD:
                    # Determine current effective cooldown (per-alert override or condition default).
                    current_hours = (
                        alert.effective_cooldown_hours
                        if alert.effective_cooldown_hours is not None
                        else _REACTIVATE_COOLDOWN_HOURS.get(alert.condition_type, 4)
                    )
                    new_hours = min(current_hours * 2, MAX_EFFECTIVE_COOLDOWN_HOURS)
                    if new_hours != alert.effective_cooldown_hours:
                        alert.effective_cooldown_hours = new_hours
                        logger.info(
                            "alert_service.record_reaction.cooldown_escalated",
                            alert_id=alert_id,
                            ticker=alert.ticker,
                            user_id=user_id,
                            dismiss_count=alert.dismiss_count,
                            old_hours=current_hours,
                            new_hours=new_hours,
                        )

            self._session.add(alert)
            await self._session.flush()

            logger.info(
                "alert_service.record_reaction.done",
                alert_id=alert_id,
                ticker=alert.ticker,
                reaction=reaction,
                reaction_count=alert.reaction_count,
                dismiss_count=alert.dismiss_count,
                effective_cooldown_hours=alert.effective_cooldown_hours,
            )

        except Exception as exc:
            logger.warning(
                "alert_service.record_reaction.error",
                alert_id=alert_id,
                user_id=user_id,
                reaction=reaction,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_owned(self, alert_id: int, user_id: str) -> Alert:
        """Fetch an alert by PK and verify ownership."""
        stmt = select(Alert).where(Alert.id == alert_id).where(Alert.user_id == user_id)
        result = await self._session.execute(stmt)
        alert = result.scalar_one_or_none()
        if alert is None:
            raise AlertNotFoundError(f"Alert {alert_id} not found for user {user_id}")
        return alert
