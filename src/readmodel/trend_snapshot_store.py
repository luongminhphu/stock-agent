"""TrendSnapshotStore — persist last TechnicalSignalBundle per symbol.

Owner: readmodel segment.
Responsibility: read concern only — stores and retrieves the last known
TechnicalSignalBundle per symbol so TrendShiftDetector can compare
current vs previous without re-fetching market data.

Boundary:
  - NEVER contains domain logic (no shift detection here).
  - NEVER imports market.TrendEngine or any market domain service.
  - Receives TechnicalSignalBundle as a plain dict payload (decoupled from
    market.trend_engine import) to avoid cross-segment model coupling.
    Callers serialize via .model_dump(); store reconstructs via TypeAdapter.

Storage strategy:
  Wave 1: in-memory dict (_cache) — survives bot restarts within the same
          process session but resets on full restart. Good enough for Phase 1
          since cold-start merely skips one scan cycle.
  Wave 2: add async DB persistence (JSON column in a trend_snapshots table)
          so snapshots survive bot restarts. See _persist_stub().

Thread safety:
  asyncio single-threaded — no locking needed for the cache dict.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.platform.logging import get_logger

logger = get_logger(__name__)


class TrendSnapshotStore:
    """In-memory snapshot store for TechnicalSignalBundle.

    Stores bundles as raw dicts to avoid importing market.trend_engine
    (cross-segment model coupling). Callers pass bundle.model_dump().
    """

    def __init__(self, session_factory: Any = None) -> None:
        # symbol (upper) → {bundle_dict, saved_at}
        self._cache: dict[str, dict[str, Any]] = {}
        self._session_factory = session_factory  # reserved for Wave 2 DB persist

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_last(self, symbol: str) -> dict[str, Any] | None:
        """Return last saved bundle dict for symbol, or None (cold start)."""
        return self._cache.get(symbol.upper())

    def get_composite(self, symbol: str) -> float | None:
        """Shortcut: return composite score from last snapshot, or None."""
        entry = self._cache.get(symbol.upper())
        if entry is None:
            return None
        return entry.get("bundle", {}).get("composite")

    def get_regime(self, symbol: str) -> str | None:
        """Shortcut: return regime label from last snapshot, or None."""
        entry = self._cache.get(symbol.upper())
        if entry is None:
            return None
        return entry.get("bundle", {}).get("regime")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, symbol: str, bundle_dict: dict[str, Any]) -> None:
        """Upsert bundle dict for symbol. Caller passes bundle.model_dump()."""
        self._cache[symbol.upper()] = {
            "bundle": bundle_dict,
            "saved_at": datetime.now(UTC).isoformat(),
        }
        logger.debug(
            "trend_snapshot_store.saved",
            symbol=symbol.upper(),
            regime=bundle_dict.get("regime"),
            composite=bundle_dict.get("composite"),
        )
        # Wave 2: await self._persist_to_db(symbol, bundle_dict)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def all_symbols(self) -> list[str]:
        """Return all symbols currently tracked in the cache."""
        return list(self._cache.keys())

    def snapshot_age_seconds(self, symbol: str) -> float | None:
        """Return age in seconds of the last snapshot, or None if not found."""
        entry = self._cache.get(symbol.upper())
        if entry is None:
            return None
        saved_at = datetime.fromisoformat(entry["saved_at"])
        return (datetime.now(UTC) - saved_at).total_seconds()

    # ------------------------------------------------------------------
    # Wave 2 stub
    # ------------------------------------------------------------------

    async def _persist_to_db(self, symbol: str, bundle_dict: dict[str, Any]) -> None:  # noqa: ARG002
        """Wave 2: persist snapshot to DB so it survives bot restarts.

        Stub — no-op until Wave 2. Session factory injected at construction.
        """
        # async with self._session_factory() as session:
        #     await TrendSnapshotRepo(session).upsert(symbol, bundle_dict)
        #     await session.commit()
        pass
