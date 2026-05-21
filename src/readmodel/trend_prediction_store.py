"""TrendPredictionStore — persist last TrendPrediction per symbol.

Owner: readmodel segment.
Responsibility: read concern only — stores and retrieves the last known
TrendPrediction per symbol so bot, briefing, and API can query verdicts
without re-running the AI engine.

Boundary:
  - NEVER contains domain logic (no trend analysis here).
  - NEVER imports ai.TrendReasoningAgent or market.TrendEngine.
  - Receives TrendPrediction as an opaque object; callers own the type.
    Store holds the object reference — no serialization in Wave 1.

Storage strategy:
  Wave 1: in-memory dict (_cache) — resets on bot restart.
          Good enough for Phase 1; cold-start skips one scan cycle.
  Wave 2: add async DB persistence (JSON column in trend_predictions table).
          See _persist_stub().

Thread safety:
  asyncio single-threaded — no locking needed for the cache dict.

Pattern: mirrors TrendSnapshotStore (same segment, same Wave-1 strategy).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.platform.logging import get_logger

logger = get_logger(__name__)


class TrendPredictionStore:
    """In-memory store for TrendPrediction objects.

    Holds the latest TrendPrediction per symbol. Callers pass the
    prediction object directly — no model_dump() required (unlike
    TrendSnapshotStore which stores raw dicts to avoid cross-segment
    model coupling). TrendPrediction is owned by ai segment; store
    holds the reference without importing its type.
    """

    def __init__(self, session_factory: Any = None) -> None:
        # symbol (upper) → {prediction, saved_at}
        self._cache: dict[str, dict[str, Any]] = {}
        self._session_factory = session_factory  # reserved for Wave 2 DB persist

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, symbol: str) -> Any | None:
        """Return last TrendPrediction for symbol, or None (cold start)."""
        entry = self._cache.get(symbol.upper())
        if entry is None:
            return None
        return entry.get("prediction")

    def get_verdict(self, symbol: str) -> str | None:
        """Shortcut: return verdict string from last prediction, or None."""
        pred = self.get(symbol)
        if pred is None:
            return None
        return getattr(pred, "verdict", None)

    def get_confidence(self, symbol: str) -> float | None:
        """Shortcut: return confidence from last prediction, or None."""
        pred = self.get(symbol)
        if pred is None:
            return None
        return getattr(pred, "confidence", None)

    def get_top_by_confidence(self, n: int = 3) -> list[Any]:
        """Return top-N TrendPredictions sorted by confidence descending.

        Used by briefing injection to surface the strongest verdicts.
        """
        predictions = [
            entry["prediction"]
            for entry in self._cache.values()
            if entry.get("prediction") is not None
        ]
        return sorted(
            predictions,
            key=lambda p: getattr(p, "confidence", 0.0),
            reverse=True,
        )[:n]

    def all_symbols(self) -> list[str]:
        """Return all symbols currently tracked in the cache."""
        return list(self._cache.keys())

    def prediction_age_seconds(self, symbol: str) -> float | None:
        """Return age in seconds of the last prediction, or None if not found."""
        entry = self._cache.get(symbol.upper())
        if entry is None:
            return None
        saved_at = datetime.fromisoformat(entry["saved_at"])
        return (datetime.now(UTC) - saved_at).total_seconds()

    def is_stale(self, symbol: str, max_age_seconds: float = 14400.0) -> bool:
        """Return True if the last prediction is older than max_age_seconds.

        Default threshold: 4 hours (14400s). Consumers should flag stale
        predictions rather than suppress them.
        """
        age = self.prediction_age_seconds(symbol)
        if age is None:
            return True
        return age > max_age_seconds

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, symbol: str, prediction: Any) -> None:
        """Upsert TrendPrediction for symbol."""
        self._cache[symbol.upper()] = {
            "prediction": prediction,
            "saved_at": datetime.now(UTC).isoformat(),
        }
        logger.debug(
            "trend_prediction_store.saved",
            symbol=symbol.upper(),
            verdict=getattr(prediction, "verdict", None),
            confidence=getattr(prediction, "confidence", None),
            direction=getattr(prediction, "direction", None),
        )
        # Wave 2: await self._persist_to_db(symbol, prediction)

    # ------------------------------------------------------------------
    # Wave 2 stub
    # ------------------------------------------------------------------

    async def _persist_to_db(self, symbol: str, prediction: Any) -> None:  # noqa: ARG002
        """Wave 2: persist prediction to DB so it survives bot restarts.

        Stub — no-op until Wave 2. Session factory injected at construction.
        """
        # async with self._session_factory() as session:
        #     await TrendPredictionRepo(session).upsert(symbol, prediction.model_dump())
        #     await session.commit()
        pass
