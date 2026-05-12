"""ReminderService — owns the full lifecycle of Reminder entities.

Owner: watchlist segment.

Responsibilities:
  - Upsert a reminder for a watchlist item (create or update frequency/enabled)
  - Enable / disable a reminder
  - Mark a reminder as sent (update last_sent_at)
  - List reminders that are due for a scheduler tick

Does NOT send Discord notifications — that is a bot/adapter concern.
Callers (scheduler, bot) receive the list of due Reminder objects and
dispatch notifications themselves.

Boundary with ScanService:
  ScanService detects price signals → may decide to trigger ON_SIGNAL reminders.
  It calls ReminderService.list_due_for_signal() and mark_sent() after
  dispatching. ReminderService never calls ScanService.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.watchlist.models import Reminder, ReminderFrequency, WatchlistItem
from src.watchlist.repository import WatchlistRepository

logger = get_logger(__name__)

# How long to wait between sends per frequency type.
_FREQUENCY_DELTA: dict[ReminderFrequency, timedelta] = {
    ReminderFrequency.DAILY: timedelta(hours=20),   # allow slight drift
    ReminderFrequency.WEEKLY: timedelta(days=6, hours=20),
    ReminderFrequency.ON_SIGNAL: timedelta(hours=1),  # cooldown between signal pings
}


class ReminderNotFoundError(Exception):
    """Raised when a Reminder cannot be found for the given item."""


class ReminderService:
    """Manages Reminder lifecycle within the watchlist segment.

    Owner: watchlist segment.
    Callers: WatchlistService (upsert/toggle), scheduler (list_due / mark_sent),
             ScanService (list_due_for_signal / mark_sent).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = WatchlistRepository(session)

    # ------------------------------------------------------------------
    # Upsert — create or update
    # ------------------------------------------------------------------

    async def upsert(
        self,
        user_id: str,
        watchlist_item: WatchlistItem,
        frequency: ReminderFrequency = ReminderFrequency.ON_SIGNAL,
        enabled: bool = True,
    ) -> Reminder:
        """Create a new Reminder or update frequency/enabled on the existing one.

        One WatchlistItem → at most one Reminder (enforced by FK + uselist=False).

        Args:
            user_id: Owner of the reminder.
            watchlist_item: The WatchlistItem this reminder belongs to.
            frequency: How often to remind. Defaults to ON_SIGNAL.
            enabled: Whether the reminder is active. Defaults to True.

        Returns:
            Persisted Reminder instance.
        """
        reminder = await self._repo.get_reminder(watchlist_item.id)

        if reminder is None:
            reminder = Reminder(
                user_id=user_id,
                watchlist_item_id=watchlist_item.id,
                frequency=frequency,
                enabled=enabled,
            )
            logger.info(
                "reminder_service.created",
                user_id=user_id,
                ticker=watchlist_item.ticker,
                frequency=frequency,
            )
        else:
            reminder.frequency = frequency
            reminder.enabled = enabled
            logger.info(
                "reminder_service.updated",
                user_id=user_id,
                ticker=watchlist_item.ticker,
                frequency=frequency,
                enabled=enabled,
            )

        await self._repo.save_reminder(reminder)
        return reminder

    # ------------------------------------------------------------------
    # Enable / disable
    # ------------------------------------------------------------------

    async def set_enabled(self, watchlist_item_id: int, *, enabled: bool) -> Reminder:
        """Toggle a reminder on or off.

        Args:
            watchlist_item_id: FK to WatchlistItem.
            enabled: True to enable, False to disable.

        Returns:
            Updated Reminder.

        Raises:
            ReminderNotFoundError: If no reminder exists for this item.
        """
        reminder = await self._repo.get_reminder(watchlist_item_id)
        if reminder is None:
            raise ReminderNotFoundError(
                f"No reminder found for watchlist item {watchlist_item_id}"
            )

        reminder.enabled = enabled
        await self._repo.save_reminder(reminder)
        logger.info(
            "reminder_service.toggled",
            watchlist_item_id=watchlist_item_id,
            enabled=enabled,
        )
        return reminder

    # ------------------------------------------------------------------
    # Mark sent
    # ------------------------------------------------------------------

    async def mark_sent(self, reminder: Reminder) -> Reminder:
        """Record that this reminder was just dispatched.

        Updates last_sent_at to now(UTC). Caller must commit the session.

        Use this when the caller already holds a live Reminder object
        from the *same* session (e.g. ScanService._fire_on_signal_reminders).
        When the Reminder object originated from a *different* (now-closed)
        session use mark_sent_by_id() instead to avoid DetachedInstanceError.

        Args:
            reminder: The Reminder that was sent (must be attached to this session).

        Returns:
            Updated Reminder with last_sent_at set.
        """
        reminder.last_sent_at = datetime.now(tz=UTC)
        await self._repo.save_reminder(reminder)
        logger.info(
            "reminder_service.mark_sent",
            reminder_id=reminder.id,
            user_id=reminder.user_id,
            frequency=reminder.frequency,
        )
        return reminder

    async def mark_sent_by_id(self, reminder_id: int) -> Reminder:
        """Record that a reminder was just dispatched, looked up by PK.

        Safe to call from a *different* session than the one that fetched
        the Reminder — queries by PK within the current session so there
        is no risk of DetachedInstanceError.

        Use this in ReminderScheduler._fire_reminders where list_due() is
        called in a read session that is closed before mark_sent runs.

        Args:
            reminder_id: Primary key of the Reminder to update.

        Returns:
            Updated Reminder with last_sent_at set.

        Raises:
            ReminderNotFoundError: If no Reminder with this PK exists.
        """
        stmt = select(Reminder).where(Reminder.id == reminder_id)
        result = await self._session.execute(stmt)
        reminder = result.scalar_one_or_none()
        if reminder is None:
            raise ReminderNotFoundError(f"Reminder {reminder_id} not found")

        reminder.last_sent_at = datetime.now(tz=UTC)
        self._session.add(reminder)
        await self._session.flush()
        logger.info(
            "reminder_service.mark_sent",
            reminder_id=reminder.id,
            user_id=reminder.user_id,
            frequency=reminder.frequency,
        )
        return reminder

    # ------------------------------------------------------------------
    # List due — for scheduler (DAILY / WEEKLY)
    # ------------------------------------------------------------------

    async def list_due(
        self,
        frequencies: list[ReminderFrequency] | None = None,
    ) -> list[Reminder]:
        """Return enabled reminders that are due to be sent now.

        Performs a coarse DB filter (last_sent_at IS NULL or < now),
        then applies precise frequency-delta check in Python.

        Args:
            frequencies: If provided, only return reminders matching these
                         frequency values. Defaults to DAILY + WEEKLY.

        Returns:
            List of due Reminder objects (with watchlist_item loaded).
        """
        if frequencies is None:
            frequencies = [ReminderFrequency.DAILY, ReminderFrequency.WEEKLY]

        now = datetime.now(tz=UTC)
        candidates = await self._repo.list_due_reminders(before=now)

        due = [
            r for r in candidates
            if r.frequency in frequencies and self._is_due(r, now)
        ]

        logger.info(
            "reminder_service.list_due",
            frequencies=[f.value for f in frequencies],
            candidates=len(candidates),
            due=len(due),
        )
        return due

    # ------------------------------------------------------------------
    # List due for signal — for ScanService
    # ------------------------------------------------------------------

    async def list_due_for_signal(self, tickers: list[str]) -> list[Reminder]:
        """Return ON_SIGNAL reminders for the given tickers that are past cooldown.

        Called by ScanService after detecting signals, to find which reminders
        should fire alongside the signal notification.

        Args:
            tickers: Tickers for which a signal was detected.

        Returns:
            List of ON_SIGNAL Reminders that are past their 1-hour cooldown.
        """
        now = datetime.now(tz=UTC)
        candidates = await self._repo.list_due_reminders(before=now)

        due = [
            r for r in candidates
            if r.frequency == ReminderFrequency.ON_SIGNAL
            and r.watchlist_item is not None
            and r.watchlist_item.ticker in tickers
            and self._is_due(r, now)
        ]

        logger.info(
            "reminder_service.list_due_for_signal",
            tickers=tickers,
            due=len(due),
        )
        return due

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_due(reminder: Reminder, now: datetime) -> bool:
        """Return True if enough time has passed since last_sent_at.

        A reminder that has never been sent (last_sent_at IS NULL) is
        always considered due.
        """
        if reminder.last_sent_at is None:
            return True
        delta = _FREQUENCY_DELTA.get(reminder.frequency, timedelta(hours=24))
        last_sent = reminder.last_sent_at
        # Ensure both are timezone-aware for safe comparison
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=UTC)
        return (now - last_sent) >= delta
