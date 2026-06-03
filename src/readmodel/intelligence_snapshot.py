"""Intelligence Snapshot Store — readmodel cache for IntelligenceReport.

Owner: readmodel segment.

Purpose:
    After core/engine.py produces an IntelligenceReport (via multi-agent
    orchestration or heuristic fallback), downstream consumers (bot, api)
    should NOT re-trigger the AI cycle on every query. This store provides
    a fast read path:

        bot:  snapshot = await store.get(user_id)  # returns latest cached report
        api:  GET /intelligence -> reads from here, never calls engine directly

Design:
    Two-layer cache:
    1. Hot layer   — DashboardTTLCache, TTL=300s. Fast in-process dict lookup.
                     Evicted automatically on expiry or explicit invalidation.
    2. Warm layer  — _long_term dict, no TTL. Holds the last known report
                     indefinitely so stale-while-revalidate is possible:
                     if hot layer miss, return warm layer + flag as stale.

    This means:
    - get() always returns something after the first cycle (no empty-hand
      responses to user once a report has been generated).
    - is_stale flag tells the caller to schedule a background refresh.

Usage::

    from src.readmodel.intelligence_snapshot import get_intelligence_snapshot

    store = get_intelligence_snapshot()

    # After engine.run_cycle() — called by core/engine.py or scheduler:
    await store.upsert(user_id, engine_output.intelligence_report)

    # In bot / api handler:
    result = await store.get(user_id)
    if result is None:
        # No report yet — engine hasn't run for this user
        return None
    report, is_stale = result
    if is_stale:
        # Optionally: trigger background refresh via scheduler
        pass

Invalidation::

    store.invalidate(user_id)       # evict hot layer only (warm remains)
    store.invalidate_all()          # nuclear — clears both layers
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.readmodel.cache import DashboardTTLCache

if TYPE_CHECKING:
    from src.ai.schemas import IntelligenceReport

_NAMESPACE = "intelligence_report"
_HOT_TTL_SECONDS = 300  # 5 min hot cache


class IntelligenceSnapshotStore:
    """Readmodel cache for the latest IntelligenceReport per user.

    Lifecycle:
        upsert()  — called by engine after every run_cycle()
        get()     — called by bot/api to read latest report
        invalidate() — called on explicit refresh requests
    """

    def __init__(self, cache: DashboardTTLCache | None = None) -> None:
        self._cache = cache or DashboardTTLCache()
        # warm layer: user_id -> (report, stored_at)
        self._warm: dict[str, tuple[IntelligenceReport, datetime]] = {}

    # ------------------------------------------------------------------
    # Write path — called by core/engine.py after run_cycle()
    # ------------------------------------------------------------------

    async def upsert(
        self,
        user_id: str,
        report: IntelligenceReport,
    ) -> None:
        """Store a fresh IntelligenceReport for user_id.

        Updates both hot (TTL) and warm (indefinite) layers.
        """
        self._cache.set(
            _NAMESPACE,
            user_id,
            report,
            ttl=_HOT_TTL_SECONDS,
        )
        self._warm[user_id] = (report, datetime.now(UTC))

    # ------------------------------------------------------------------
    # Read path — called by bot/api
    # ------------------------------------------------------------------

    async def get(
        self,
        user_id: str,
    ) -> tuple[IntelligenceReport, bool] | None:
        """Return (report, is_stale) or None if no report exists yet.

        is_stale=False  — hot cache hit (report is fresh, <= 300s old)
        is_stale=True   — hot cache miss but warm layer has a previous
                          report; caller should schedule a background
                          refresh but can still render the stale data.
        Returns None only if no report has ever been generated for user.
        """
        hot = self._cache.get(_NAMESPACE, user_id)
        if hot is not None:
            return hot, False

        warm_entry = self._warm.get(user_id)
        if warm_entry is not None:
            report, _ = warm_entry
            return report, True

        return None

    async def get_or_none(
        self,
        user_id: str,
    ) -> IntelligenceReport | None:
        """Convenience: return report regardless of staleness, or None."""
        result = await self.get(user_id)
        if result is None:
            return None
        report, _ = result
        return report

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------

    def last_updated_at(self, user_id: str) -> datetime | None:
        """Return the timestamp when the report was last upserted, or None."""
        entry = self._warm.get(user_id)
        if entry is None:
            return None
        _, stored_at = entry
        return stored_at

    def is_stale(self, user_id: str) -> bool | None:
        """True if warm entry exists but hot cache has expired. None if no entry."""
        if user_id not in self._warm:
            return None
        hot = self._cache.get(_NAMESPACE, user_id)
        return hot is None

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def invalidate(self, user_id: str) -> None:
        """Evict hot layer for user. Warm layer is preserved for stale reads."""
        self._cache.invalidate(_NAMESPACE, user_id)

    def invalidate_all(self) -> None:
        """Clear both layers entirely."""
        self._cache.invalidate_all()
        self._warm.clear()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def active_users(self) -> list[str]:
        """Return user_ids that have at least one warm-layer entry."""
        return list(self._warm.keys())

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"IntelligenceSnapshotStore("
            f"warm_users={len(self._warm)}, "
            f"hot_alive={self._cache.alive_size()})"
        )


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_snapshot_store: IntelligenceSnapshotStore | None = None


def get_intelligence_snapshot() -> IntelligenceSnapshotStore:
    """Return the process-level IntelligenceSnapshotStore singleton.

    Creates on first call. Safe within a single asyncio event loop.
    Inject a custom DashboardTTLCache in tests::

        store = IntelligenceSnapshotStore(cache=DashboardTTLCache())
    """
    global _snapshot_store
    if _snapshot_store is None:
        _snapshot_store = IntelligenceSnapshotStore()
    return _snapshot_store
