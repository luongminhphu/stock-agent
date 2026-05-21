"""TTL Cache — platform segment.

Lightweight in-process async TTL cache với stampede protection.
Không có external dependency (no Redis, no diskcache).

Owner: platform segment.
Consumers: market segment (news, regime), ai segment (prediction cache tương lai).

Design:
- AsyncTTLCache[K, V]: generic, type-safe
- Stampede protection: asyncio.Event per key — chỉ 1 coroutine fetch thật,
  các coroutine còn lại await event rồi đọc kết quả từ store
- Thread-safe: single-threaded asyncio event loop — không cần Lock
- Eviction: lazy (check khi get), không có background sweep
- Không persist qua restart — đây là design choice, không phải bug
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from src.platform.logging import get_logger

logger = get_logger(__name__)

K = TypeVar("K")
V = TypeVar("V")


@dataclass
class _CacheEntry(Generic[V]):
    value: V
    expires_at: float  # monotonic time


@dataclass
class _InFlight(Generic[V]):
    """Marker cho stampede protection — key đang được fetch."""

    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: V | None = None
    error: BaseException | None = None


class AsyncTTLCache(Generic[K, V]):
    """Async TTL cache với stampede protection.

    Usage::

        cache: AsyncTTLCache[str, list[NewsItem]] = AsyncTTLCache(ttl=900)

        async def get_news(symbol: str) -> list[NewsItem]:
            return await cache.get_or_fetch(
                key=symbol,
                fetch=lambda: adapter.get_news(symbol),
            )
    """

    def __init__(self, ttl: float, name: str = "ttl_cache") -> None:
        self._ttl = ttl
        self._name = name
        self._store: dict[K, _CacheEntry[V]] = {}
        self._in_flight: dict[K, _InFlight[V]] = {}

    async def get_or_fetch(
        self,
        key: K,
        fetch: Callable[[], Awaitable[V]],
    ) -> V:
        """Trả cached value hoặc gọi fetch(). Stampede-safe."""

        # 1. Cache hit
        entry = self._store.get(key)
        if entry is not None and time.monotonic() < entry.expires_at:
            logger.debug(f"{self._name}.cache_hit", extra={"key": key})
            return entry.value

        # 2. Đang có coroutine khác fetch cùng key → piggyback
        if key in self._in_flight:
            logger.debug(f"{self._name}.piggyback", extra={"key": key})
            flight = self._in_flight[key]
            await flight.event.wait()
            if flight.error is not None:
                raise flight.error
            return flight.result  # type: ignore[return-value]

        # 3. Tôi là người đầu tiên — fetch thật sự
        flight: _InFlight[V] = _InFlight()
        self._in_flight[key] = flight

        try:
            value = await fetch()
            self._store[key] = _CacheEntry(
                value=value,
                expires_at=time.monotonic() + self._ttl,
            )
            flight.result = value
            logger.debug(f"{self._name}.fetched", extra={"key": key, "ttl": self._ttl})
            return value

        except Exception as exc:
            self._store.pop(key, None)
            flight.error = exc
            logger.warning(f"{self._name}.fetch_failed", extra={"key": key, "error": str(exc)})
            raise

        finally:
            # Luôn release waiters dù thành công hay thất bại
            flight.event.set()
            self._in_flight.pop(key, None)

    def invalidate(self, key: K) -> None:
        """Xóa 1 key thủ công — dùng khi có breaking news."""
        self._store.pop(key, None)

    def invalidate_all(self) -> None:
        """Xóa toàn bộ cache — dùng sau market close."""
        self._store.clear()

    def stats(self) -> dict:
        now = time.monotonic()
        live = sum(1 for e in self._store.values() if now < e.expires_at)
        return {
            "name": self._name,
            "total_entries": len(self._store),
            "live_entries": live,
            "in_flight": len(self._in_flight),
            "ttl_seconds": self._ttl,
        }
