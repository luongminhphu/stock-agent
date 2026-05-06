"""
In-Process Async Event Bus — Platform V2
Lightweight pub/sub using asyncio.Queue. No external broker required.

Design decisions:
- Single global bus instance (singleton via get_event_bus()).
- Handlers are async coroutines; sync handlers are wrapped automatically.
- Dead-letter queue for failed handlers — never silently swallow errors.
- Dedup window per (event_type, dedup_key) to prevent signal spam.
- asyncio-native; does not support threading (use thread-safe queue wrapper if needed).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine, Type, TypeVar

from .events import DomainEvent

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=DomainEvent)
Handler = Callable[[DomainEvent], Coroutine[Any, Any, None]]

# Dedup window: same (event_type, dedup_key) won't re-trigger within this window.
DEFAULT_DEDUP_WINDOW = timedelta(minutes=60)


class DeadLetterEntry:
    def __init__(self, event: DomainEvent, handler_name: str, error: Exception):
        self.event = event
        self.handler_name = handler_name
        self.error = error
        self.failed_at = datetime.utcnow()

    def __repr__(self) -> str:
        return (
            f"DeadLetterEntry(event={type(self.event).__name__}, "
            f"handler={self.handler_name}, error={self.error!r})"
        )


class EventBus:
    """
    Async in-process event bus.

    Usage:
        bus = get_event_bus()

        @bus.subscribe(SignalDetectedEvent)
        async def handle_signal(event: SignalDetectedEvent):
            ...

        await bus.publish(SignalDetectedEvent(symbol="VCB", ...))
    """

    def __init__(self) -> None:
        self._handlers: dict[Type[DomainEvent], list[Handler]] = defaultdict(list)
        self._queue: asyncio.Queue[DomainEvent] = asyncio.Queue()
        self._dedup: dict[str, datetime] = {}           # dedup_key → last_seen
        self._dead_letters: list[DeadLetterEntry] = []
        self._running = False
        self._worker_task: asyncio.Task | None = None

    # ── subscription ──────────────────────────────────────────────────────────

    def subscribe(
        self,
        event_type: Type[T],
        dedup_window: timedelta | None = None,
    ) -> Callable[[Handler], Handler]:
        """
        Decorator to register an async handler for an event type.

            @bus.subscribe(SignalDetectedEvent)
            async def my_handler(event: SignalDetectedEvent): ...
        """
        def decorator(fn: Handler) -> Handler:
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(
                    f"Handler {fn.__name__!r} must be an async function. "
                    "Use `async def` or wrap a sync function with asyncio.to_thread."
                )
            self._handlers[event_type].append(fn)
            logger.debug("Subscribed %s → %s", event_type.__name__, fn.__name__)
            return fn
        return decorator

    def subscribe_handler(self, event_type: Type[T], handler: Handler) -> None:
        """Programmatic subscription (no decorator syntax)."""
        if not inspect.iscoroutinefunction(handler):
            raise TypeError(f"Handler {handler.__name__!r} must be async.")
        self._handlers[event_type].append(handler)

    # ── publishing ────────────────────────────────────────────────────────────

    async def publish(
        self,
        event: DomainEvent,
        dedup_key: str | None = None,
        dedup_window: timedelta = DEFAULT_DEDUP_WINDOW,
    ) -> bool:
        """
        Enqueue an event for delivery.

        Args:
            event:        The domain event to publish.
            dedup_key:    Optional dedup key. If provided, duplicate events
                          within `dedup_window` are silently dropped.
                          Useful for signal spam prevention (e.g. same symbol + signal_type).
            dedup_window: How long to suppress re-delivery for the same dedup_key.

        Returns:
            True if the event was enqueued, False if deduplicated.
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

    def publish_sync(
        self,
        event: DomainEvent,
        dedup_key: str | None = None,
        dedup_window: timedelta = DEFAULT_DEDUP_WINDOW,
    ) -> None:
        """
        Thread-safe synchronous publish. Use only from non-async contexts.
        Requires the bus worker loop to be running.
        """
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    self.publish(event, dedup_key, dedup_window)
                )
            )
        else:
            loop.run_until_complete(self.publish(event, dedup_key, dedup_window))

    # ── worker loop ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background event worker. Call once at app startup."""
        if self._running:
            logger.warning("EventBus.start() called while already running.")
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker(), name="event-bus-worker")
        logger.info("EventBus started.")

    async def stop(self) -> None:
        """Graceful shutdown: drain the queue then stop the worker."""
        self._running = False
        await self._queue.join()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("EventBus stopped. Dead letters: %d", len(self._dead_letters))

    async def _worker(self) -> None:
        while self._running or not self._queue.empty():
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await self._dispatch(event)
            self._queue.task_done()

    async def _dispatch(self, event: DomainEvent) -> None:
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            logger.debug("No handlers for %s — event dropped.", type(event).__name__)
            return

        for handler in handlers:
            try:
                await handler(event)
            except Exception as exc:
                logger.exception(
                    "Handler %s failed for %s (id=%s): %s",
                    handler.__name__, type(event).__name__, event.event_id, exc,
                )
                self._dead_letters.append(
                    DeadLetterEntry(event, handler.__name__, exc)
                )

    # ── observability ─────────────────────────────────────────────────────────

    @property
    def dead_letters(self) -> list[DeadLetterEntry]:
        return list(self._dead_letters)

    def clear_dead_letters(self) -> int:
        n = len(self._dead_letters)
        self._dead_letters.clear()
        return n

    def stats(self) -> dict[str, Any]:
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
    """Return the global EventBus singleton. Thread-safe initialization."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_event_bus() -> None:
    """Reset the singleton — for use in tests only."""
    global _bus
    _bus = None
