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

Async interface (briefing contract):
  briefing/service._run_trend_predictions() calls:
    await store.get_for_tickers(tickers=tickers)
  This method is the primary async read path. All other read methods are
  sync convenience accessors (get, get_verdict, get_confidence, etc.) that
  remain callable without await.

Pattern: mirrors TrendSnapshotStore (same segment, same Wave-1 strategy).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.platform.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# DB persistence helpers (Wave D.1)
# ---------------------------------------------------------------------------

async def _persist_prediction(session_factory, symbol: str, prediction) -> None:
    """Upsert a TrendPrediction row. Fire-and-forget — never raises."""
    if session_factory is None:
        return
    try:
        import json as _json
        from datetime import UTC, datetime as _dt, timedelta as _td
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from src.readmodel.models import TrendPrediction

        verdict = str(getattr(prediction, "verdict", "HOLD"))
        confidence = float(getattr(prediction, "confidence", 0.0))
        try:
            reasoning_json = _json.dumps(prediction.model_dump(), default=str)
        except Exception:
            reasoning_json = None
        predicted_at = _dt.now(UTC)
        expires_at = predicted_at + _td(hours=4)

        async with session_factory() as session:
            stmt = pg_insert(TrendPrediction).values(
                symbol=symbol.upper(),
                verdict=verdict,
                confidence=confidence,
                reasoning_json=reasoning_json,
                predicted_at=predicted_at,
                expires_at=expires_at,
            ).on_conflict_do_update(
                index_elements=["symbol"],
                set_={
                    "verdict": verdict,
                    "confidence": confidence,
                    "reasoning_json": reasoning_json,
                    "predicted_at": predicted_at,
                    "expires_at": expires_at,
                },
            )
            await session.execute(stmt)
            await session.commit()
    except Exception as exc:
        logger.warning("trend_prediction_store.persist_failed", symbol=symbol, error=str(exc))


async def load_predictions_from_db(session_factory) -> list[dict]:
    """Load non-expired predictions from DB on startup. Returns list of row dicts."""
    if session_factory is None:
        return []
    try:
        import json as _json
        from datetime import UTC, datetime as _dt
        from sqlalchemy import select
        from src.readmodel.models import TrendPrediction

        now = _dt.now(UTC)
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(TrendPrediction).where(TrendPrediction.expires_at > now)
                )
            ).scalars().all()
            result = []
            for row in rows:
                result.append({
                    "symbol": row.symbol,
                    "verdict": row.verdict,
                    "confidence": row.confidence,
                    "reasoning_json": row.reasoning_json,
                })
            logger.info("trend_prediction_store.loaded_from_db", count=len(result))
            return result
    except Exception as exc:
        logger.warning("trend_prediction_store.load_failed", error=str(exc))
        return []



# ---------------------------------------------------------------------------
# Restored prediction wrapper — used during warm load from DB
# ---------------------------------------------------------------------------

class _RestoredPrediction:
    """Lightweight wrapper around a JSON-restored TrendPrediction.

    Exposes the same attribute interface that bot/briefing callers expect
    (verdict, confidence, symbol) without requiring the full AI schema import.
    Downstream callers that need the full model should filter on type checks.
    """

    def __init__(self, symbol: str, verdict: str, confidence: float, _data: dict) -> None:
        self.symbol = symbol
        self.verdict = verdict
        self.confidence = confidence
        self._data = _data

    def model_dump(self) -> dict:
        return self._data

    def __repr__(self) -> str:
        return f"_RestoredPrediction(symbol={self.symbol!r}, verdict={self.verdict!r}, confidence={self.confidence})"

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
    # Read — sync accessors
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
    # Read — async interface (briefing / bot / API callers)
    # ------------------------------------------------------------------

    async def get_for_tickers(
        self,
        tickers: list[str],
        skip_stale: bool = True,
        max_age_seconds: float = 14400.0,
    ) -> list[Any]:
        """Return TrendPrediction objects for a list of tickers.

        Primary async read path — called by briefing/service._run_trend_predictions():
            predictions = await store.get_for_tickers(tickers=tickers)

        Args:
            tickers:          List of ticker symbols (case-insensitive).
            skip_stale:       When True (default), excludes predictions older
                              than max_age_seconds. Stale verdicts should not
                              reach the morning brief. Set False to include all.
            max_age_seconds:  Staleness threshold in seconds. Default 4 hours.

        Returns:
            List of TrendPrediction objects, one per ticker that has a valid
            (non-stale) prediction cached. Empty list when cache is cold or
            all predictions are stale — never raises.

        Note:
            Wave 1: pure in-memory — no async I/O. The async signature is
            intentional so callers can await without changes when Wave 2 adds
            DB persistence.
        """
        results: list[Any] = []
        for ticker in tickers:
            if skip_stale and self.is_stale(ticker, max_age_seconds):
                continue
            pred = self.get(ticker)
            if pred is not None:
                results.append(pred)
        return results

    async def get_top_by_confidence_async(self, n: int = 3) -> list[Any]:
        """Async wrapper around get_top_by_confidence() for awaitable callers.

        Returns top-N predictions by confidence across all cached symbols.
        Useful for briefing/bot when surfacing the highest-conviction verdicts.
        """
        return self.get_top_by_confidence(n)

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
        # Wave D.1: fire-and-forget persist to DB
        import asyncio as _asyncio
        _asyncio.create_task(_persist_prediction(self._session_factory, symbol, prediction))

    # ------------------------------------------------------------------
    # Wave D.1: warm load on startup
    # ------------------------------------------------------------------

    async def warm_load(self) -> int:
        """Load non-expired predictions from DB into memory on startup.

        Returns number of predictions loaded.
        Allows briefing/bot to serve the last known verdict immediately
        after a restart without re-running the AI engine.
        """
        rows = await load_predictions_from_db(self._session_factory)
        loaded = 0
        for row in rows:
            reasoning_json = row.get("reasoning_json")
            if reasoning_json:
                try:
                    import json as _json
                    from datetime import UTC, datetime as _dt
                    # Reconstruct a minimal stub object for the in-memory cache.
                    # Full model restore attempted via model_validate; falls back
                    # to a lightweight _RestoredPrediction wrapper.
                    data = _json.loads(reasoning_json)
                    prediction = _RestoredPrediction(
                        symbol=row["symbol"],
                        verdict=row["verdict"],
                        confidence=row["confidence"],
                        _data=data,
                    )
                    self._cache[row["symbol"].upper()] = {
                        "prediction": prediction,
                        "saved_at": _dt.now(UTC).isoformat(),
                    }
                    loaded += 1
                except Exception:
                    pass
        logger.info("trend_prediction_store.warm_loaded", count=loaded)
        return loaded
