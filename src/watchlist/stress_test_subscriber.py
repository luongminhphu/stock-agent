"""StressTestSubscriber — Watchlist Segment, Wave 2.

Owner: watchlist segment.

Subscribes to platform.StressTestCompletedEvent.
Parses suggested_triggers from AI stress-test output.
Auto-creates ThesisTriggerAlert rules via AlertService.

Boundary rules:
- Does NOT import thesis segment — only reads event fields.
- Does NOT call StressTestService or ThesisRepository directly.
- AlertService owns all persistence logic.
- register() must be called once at app bootstrap (e.g. platform/bootstrap.py).

Alert rule dedup:
    dedup_key = "stress:{thesis_id}:{trigger_index}"
    Identical key → skip silently. Safe to call register() multiple times.

Threshold defaults (override via constructor for tests):
    min_invalidation_prob = 0.25  — skip low-risk results
    max_triggers_per_test = 5     — cap noise from verbose AI output
"""

from __future__ import annotations

from src.platform.event_bus import get_event_bus
from src.platform.events import StressTestCompletedEvent
from src.platform.logging import get_logger
from src.watchlist.alert_service import AlertService

logger = get_logger(__name__)


class StressTestSubscriber:
    """Listens for StressTestCompletedEvent and creates watchlist alert rules.

    Args:
        session_factory: Async context manager that yields AsyncSession.
            Example: async_sessionmaker(engine, expire_on_commit=False)
        min_invalidation_prob: Minimum invalidation_probability to act on.
        max_triggers_per_test: Cap on number of alert rules created per event.
    """

    def __init__(
        self,
        session_factory,
        min_invalidation_prob: float = 0.25,
        max_triggers_per_test: int = 5,
    ) -> None:
        self._session_factory = session_factory
        self._min_prob = min_invalidation_prob
        self._max_triggers = max_triggers_per_test
        self._registered = False

    def register(self) -> None:
        """Register this subscriber on the global event bus.

        Idempotent — safe to call multiple times (e.g. in tests).
        """
        if self._registered:
            return
        get_event_bus().subscribe_handler(StressTestCompletedEvent, self._handle)
        self._registered = True
        logger.info(
            "stress_test_subscriber.registered",
            min_prob=self._min_prob,
            max_triggers=self._max_triggers,
        )

    async def _handle(self, event: StressTestCompletedEvent) -> None:
        """Handle a completed stress-test event.

        Creates up to max_triggers_per_test alert rules for the user.
        Skips rules that already exist (dedup_key match).
        Commits once at the end — all-or-nothing per event.
        """
        if event.invalidation_probability < self._min_prob:
            logger.info(
                "stress_test_subscriber.skip_low_risk",
                thesis_id=event.thesis_id,
                symbol=event.symbol,
                invalidation_prob=event.invalidation_probability,
                threshold=self._min_prob,
            )
            return

        if not event.suggested_triggers:
            logger.info(
                "stress_test_subscriber.no_triggers",
                thesis_id=event.thesis_id,
                symbol=event.symbol,
            )
            return

        triggers = event.suggested_triggers[: self._max_triggers]
        created = 0
        skipped = 0

        async with self._session_factory() as session:
            alert_svc = AlertService(session)

            for idx, trigger_text in enumerate(triggers):
                dedup_key = f"stress:{event.thesis_id}:{idx}"

                if await alert_svc.rule_exists_by_dedup_key(
                    user_id=event.user_id,
                    dedup_key=dedup_key,
                ):
                    skipped += 1
                    continue

                label = f"[Thesis #{event.thesis_id}] {trigger_text[:120]}"
                await alert_svc.create_thesis_trigger_rule(
                    user_id=event.user_id,
                    symbol=event.symbol,
                    label=label,
                    trigger_description=trigger_text,
                    thesis_id=event.thesis_id,
                    dedup_key=dedup_key,
                    source_event_id=event.event_id,
                    invalidation_probability=event.invalidation_probability,
                )
                created += 1

            await session.commit()

        logger.info(
            "stress_test_subscriber.done",
            thesis_id=event.thesis_id,
            symbol=event.symbol,
            user_id=event.user_id,
            created=created,
            skipped=skipped,
            total_triggers=len(triggers),
        )
