"""
In-Process Async Event Bus — Platform V2
Lightweight pub/sub using asyncio.Queue. No external broker required.

Design decisions:
- Single global bus instance (singleton via get_event_bus()).
- Handlers are async coroutines; sync handlers are not supported (wrap with asyncio.to_thread).
- Dead-letter queue for failed handlers — never silently swallow errors.
- Dedup window per (event_type, dedup_key) to prevent signal spam.
- asyncio-native; safe for single-process deployments (modular monolith).

Usage:
    from src.platform import get_event_bus
    from src.platform.events import SignalDetectedEvent

    bus = get_event_bus()

    @bus.subscribe(SignalDetectedEvent)
    async def handle_signal(event: SignalDetectedEvent):
        print(event.symbol, event.signal_type)

    # In startup (e.g. bootstrap.py):
    await bus.start()

    # Publishing:
    await bus.publish(
        SignalDetectedEvent(symbol="VCB", signal_type="BREAKOUT", strength=0.85, confidence=0.9),
        dedup_key="VCB:BREAKOUT",
    )

    # In shutdown:
    await bus.stop()
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine, Type, TypeVar

from src.platform.events import DomainEvent

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=DomainEvent)
Handler = Callable[[DomainEvent], Coroutine[Any, Any, None]]

# Default dedup window: suppress same (event_type, dedup_key) within 60 minutes.
DEFAULT_DEDUP_WINDOW = timedelta(minutes=60)


class DeadLetterEntry:
    """Holds a failed event + handler info for post-mortem inspection."""

    def __init__(self, event: DomainEvent, handler_name: str, error: Exception) -> None:
        self.event = event
        self.handler_name = handler_name
        self.error = error
        self.failed_at = datetime.utcnow()

    def __repr__(self) -> str:
        return (
            f"DeadLetterEntry(event={type(self.event).__name__!r}, "
            f"handler={self.handler_name!r}, error={self.error!r}, "
            f"failed_at={self.failed_at.isoformat()!r})"
        )


class EventBus:
    """
    Async in-process event bus.

    Lifecycle:
        bus = get_event_bus()
        await bus.start()   # call once at app startup
        ...                  # application runs
        await bus.stop()    # call once at app shutdown (drains queue gracefully)
    """

    def __init__(self) -> None:
        self._handlers: dict[Type[DomainEvent], list[Handler]] = defaultdict(list)
        self._queue: asyncio.Queue[DomainEvent] = asyncio.Queue()
        self._dedup: dict[str, datetime] = {}
        self._dead_letters: list[DeadLetterEntry] = []
        self._running = False
        self._worker_task: asyncio.Task | None = None

    # ── subscription ──────────────────────────────────────────────────────────

    def subscribe(
        self,
        event_type: Type[T],
    ) -> Callable[[Handler], Handler]:
        """
        Decorator to register an async handler for a specific event type.

        Example:
            @bus.subscribe(SignalDetectedEvent)
            async def my_handler(event: SignalDetectedEvent): ...
        """
        def decorator(fn: Handler) -> Handler:
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(
                    f"Handler {fn.__name__!r} must be an async function (async def). "
                    "Wrap sync functions with asyncio.to_thread if needed."
                )
            self._handlers[event_type].append(fn)
            logger.debug("Subscribed %s → %s", event_type.__name__, fn.__name__)
            return fn
        return decorator

    def subscribe_handler(self, event_type: Type[T], handler: Handler) -> None:
        """Programmatic (non-decorator) subscription."""
        if not inspect.iscoroutinefunction(handler):
            raise TypeError(f"Handler {handler.__name__!r} must be async.")
        self._handlers[event_type].append(handler)
        logger.debug("Subscribed %s → %s (programmatic)", event_type.__name__, handler.__name__)

    def unsubscribe_handler(self, event_type: Type[T], handler: Handler) -> bool:
        """Remove a previously registered handler. Returns True if removed."""
        handlers = self._handlers.get(event_type, [])
        try:
            handlers.remove(handler)
            return True
        except ValueError:
            return False

    # ── publishing ────────────────────────────────────────────────────────────

    async def publish(
        self,
        event: DomainEvent,
        dedup_key: str | None = None,
        dedup_window: timedelta = DEFAULT_DEDUP_WINDOW,
    ) -> bool:
        """
        Enqueue an event for async delivery.

        Args:
            event:        The domain event to publish.
            dedup_key:    Optional dedup key. If set, duplicate events with the
                          same key within `dedup_window` are silently dropped.
                          Convention: use "SYMBOL:SIGNAL_TYPE" (e.g. "VCB:BREAKOUT").
            dedup_window: Suppression window (default: 60 minutes).

        Returns:
            True if enqueued, False if deduplicated.
        """
        if dedup_key:
            full_key = f"{type(event).__name__}:{dedup_key}"
            last_seen = self._dedup.get(full_key)
            if last_seen and datetime.utcnow() - last_seen < dedup_window:
                logger.debug(
                    "Dedup suppressed %s (key=%s, window=%s)",
                    type(event).__name__, dedup_key, dedup_window,
                )
                return False
            self._dedup[full_key] = datetime.utcnow()

        await self._queue.put(event)
        logger.debug("Published %s (id=%s)", type(event).__name__, event.event_id)
        return True

    def publish_nowait(
        self,
        event: DomainEvent,
        dedup_key: str | None = None,
        dedup_window: timedelta = DEFAULT_DEDUP_WINDOW,
    ) -> bool:
        """
        Non-blocking publish for use inside a running event loop
        (e.g. from a sync callback that already has loop access).
        Does NOT support dedup — use `await publish()` for dedup support.
        """
        try:
            self._queue.put_nowait(event)
            logger.debug("Published (nowait) %s (id=%s)", type(event).__name__, event.event_id)
            return True
        except asyncio.QueueFull:
            logger.warning("EventBus queue full — event %s dropped.", type(event).__name__)
            return False

    # ── worker loop ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background dispatch worker. Call once at app startup."""
        if self._running:
            logger.warning("EventBus.start() called while already running — skipped.")
            return
        self._running = True
        self._worker_task = asyncio.create_task(
            self._worker(), name="event-bus-worker"
        )
        logger.info("EventBus started.")

    async def stop(self) -> None:
        """
        Graceful shutdown: drain all pending events then cancel the worker.
        Call once at app shutdown (e.g. FastAPI lifespan, bot on_close).
        """
        self._running = False
        await self._queue.join()           # wait for all queued events to be processed
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info(
            "EventBus stopped. Dead letters: %d", len(self._dead_letters)
        )

    async def _worker(self) -> None:
        """Background task: dequeue and dispatch events one at a time."""
        while self._running or not self._queue.empty():
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                await self._dispatch(event)
            finally:
                self._queue.task_done()

    async def _dispatch(self, event: DomainEvent) -> None:
        """Fan-out delivery to all registered handlers for this event type."""
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            logger.debug(
                "No handlers registered for %s — event dropped.",
                type(event).__name__,
            )
            return

        for handler in handlers:
            try:
                await handler(event)  # type: ignore[arg-type]
            except Exception as exc:
                logger.exception(
                    "Handler %r failed for %s (id=%s): %s",
                    handler.__name__, type(event).__name__, event.event_id, exc,
                )
                self._dead_letters.append(
                    DeadLetterEntry(event, handler.__name__, exc)
                )

    # ── observability ─────────────────────────────────────────────────────────

    @property
    def dead_letters(self) -> list[DeadLetterEntry]:
        """Snapshot of all failed handler entries."""
        return list(self._dead_letters)

    def clear_dead_letters(self) -> int:
        """Clear the dead-letter queue. Returns number of entries cleared."""
        n = len(self._dead_letters)
        self._dead_letters.clear()
        return n

    def stats(self) -> dict[str, Any]:
        """Runtime stats for health checks and monitoring."""
        return {
            "running": self._running,
            "queue_size": self._queue.qsize(),
            "registered_event_types": [t.__name__ for t in self._handlers],
            "total_handlers": sum(len(v) for v in self._handlers.values()),
            "dead_letter_count": len(self._dead_letters),
            "dedup_cache_size": len(self._dedup),
        }


# ── singleton ──────────────────────────────────────────────────────────────────

_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the global EventBus singleton. Safe to call multiple times."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_event_bus() -> None:
    """
    Reset the singleton to a fresh EventBus.
    ONLY for use in tests — never call in production code.
    """
    global _bus
    _bus = None
