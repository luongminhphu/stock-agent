"""Lightweight asyncio-native TTL cache for readmodel read paths.

Owner: readmodel segment.

Design constraints:
- Zero external dependencies (no Redis, no aiocache, no memcached).
- Safe within a single asyncio event loop (modular monolith assumption).
- Per-process, in-memory only — cache clears on restart. Acceptable for
  dashboard KPIs that are regenerated from DB every poll cycle anyway.
- Not suitable for write-through or cross-process invalidation.

Usage::

    from src.readmodel.cache import DashboardTTLCache

    _cache = DashboardTTLCache()

    async def get_stats(self, user_id: str) -> dict:
        cached = _cache.get("stats", user_id)
        if cached is not None:
            return cached
        result = await self._stats.get_stats(user_id)
        _cache.set("stats", user_id, result, ttl=60)
        return result

Invalidation::

    _cache.invalidate("stats", user_id)  # on write events
    _cache.invalidate_all()              # nuclear option
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


class DashboardTTLCache:
    """Simple TTL cache for dashboard read paths.

    Keys are (namespace, user_id, extra) tuples so different callers
    for the same user+method stay isolated.

    Not thread-safe across OS threads — safe within a single asyncio
    event loop because Python's GIL protects dict operations.
    """

    # Default TTLs (seconds) per namespace — tunable at call-site via ttl= kwarg.
    DEFAULTS: dict[str, int] = {
        "stats":           60,
        "scan_latest":     30,
        "recent_signals":  30,
        "brief_latest":    30,
        "thesis_detail":   15,
        "rrg":             600,   # 10 min — weekly OHLCV data; no point re-fetching intraday
        "attention":       30,
        "trend":           300,   # 5 min — daily OHLCV indicators; stable within a session
    }

    # Evict expired entries after this many set() calls (amortised O(1) per call).
    _EVICT_EVERY: int = 50

    def __init__(self) -> None:
        # _store: key -> (payload, expires_at)
        self._store: dict[tuple, tuple[Any, datetime]] = {}
        self._set_count: int = 0

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def get(self, namespace: str, user_id: str, extra: str = "") -> Any | None:
        """Return cached value or None if missing / expired."""
        key = (namespace, user_id, extra)
        entry = self._store.get(key)
        if entry is None:
            return None
        payload, expires_at = entry
        if datetime.now(UTC) >= expires_at:
            del self._store[key]
            return None
        return payload

    def set(
        self,
        namespace: str,
        user_id: str,
        value: Any,
        *,
        ttl: int | None = None,
        extra: str = "",
    ) -> None:
        """Store value with TTL (seconds). Uses DEFAULTS[namespace] if ttl omitted."""
        from datetime import timedelta

        effective_ttl = ttl if ttl is not None else self.DEFAULTS.get(namespace, 30)
        expires_at = datetime.now(UTC) + timedelta(seconds=effective_ttl)
        self._store[(namespace, user_id, extra)] = (value, expires_at)

        # Amortised periodic eviction: every _EVICT_EVERY set() calls, sweep expired
        # entries so memory doesn’t grow unbounded when keys are never re-read.
        self._set_count += 1
        if self._set_count % self._EVICT_EVERY == 0:
            self._evict_expired()

    def _evict_expired(self) -> None:
        """Remove all expired entries in one pass. Called amortised from set()."""
        now = datetime.now(UTC)
        expired = [k for k, (_, exp) in self._store.items() if now >= exp]
        for k in expired:
            del self._store[k]

    def invalidate(self, namespace: str, user_id: str, extra: str = "") -> None:
        """Evict a specific cache entry."""
        self._store.pop((namespace, user_id, extra), None)

    def invalidate_user(self, user_id: str) -> None:
        """Evict all entries for a given user (e.g. after a write event)."""
        keys_to_drop = [k for k in self._store if k[1] == user_id]
        for k in keys_to_drop:
            del self._store[k]

    def invalidate_all(self) -> None:
        """Nuclear option — clear entire cache."""
        self._store.clear()

    # ------------------------------------------------------------------
    # Introspection (for health checks / debug)
    # ------------------------------------------------------------------

    def size(self) -> int:
        """Number of entries currently in store (including possibly-expired)."""
        return len(self._store)

    def alive_size(self) -> int:
        """Number of non-expired entries."""
        now = datetime.now(UTC)
        return sum(1 for _, (_, exp) in self._store.items() if now < exp)

    def __repr__(self) -> str:  # pragma: no cover
        return f"DashboardTTLCache(total={self.size()}, alive={self.alive_size()})"
