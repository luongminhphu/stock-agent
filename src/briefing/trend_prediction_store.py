"""
TrendPredictionStore — in-process cache for trend predictions.

Owner: briefing segment.
Producer: TrendBatchScheduler (writes via store())
Consumer:
    - BriefingService.generate_morning_brief() (reads via get_top())
    - bot/commands/trend.py (reads via get(symbol))

Design:
    In-memory dict keyed by symbol (uppercased).
    One store instance per process — injected via bootstrap.
    No DB persistence: predictions are short-lived (4h TTL).
    Thread-safe for asyncio (single-threaded event loop).

Wave 3: replace with Redis cache if multi-worker deployment needed.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.ai.agents.trend_reasoning import TrendPrediction

logger = get_logger(__name__)

_VERDICT_PRIORITY = {
    "STRONG_BUY": 0,
    "BUY": 1,
    "WATCH": 2,
    "HOLD": 3,
    "REDUCE": 4,
    "STRONG_SELL": 5,
}


class TrendPredictionStore:
    """Lightweight in-process cache for TrendPrediction objects."""

    def __init__(self) -> None:
        self._store: dict[str, "TrendPrediction"] = {}
        self._updated_at: datetime | None = None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def store(self, predictions: list["TrendPrediction"]) -> None:
        """Replace entire cache with a fresh batch."""
        self._store = {p.symbol.upper(): p for p in predictions}
        self._updated_at = datetime.now(UTC)
        logger.info(
            "trend_prediction_store.stored",
            count=len(self._store),
            symbols=list(self._store.keys()),
        )

    def upsert(self, prediction: "TrendPrediction") -> None:
        """Update a single symbol without replacing the whole cache."""
        self._store[prediction.symbol.upper()] = prediction
        self._updated_at = datetime.now(UTC)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, symbol: str) -> "TrendPrediction | None":
        """Get prediction for a specific symbol. None if not cached."""
        return self._store.get(symbol.upper())

    def get_top(
        self,
        n: int = 5,
        verdict_filter: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> list["TrendPrediction"]:
        """Return top N predictions sorted by verdict priority then confidence.

        Args:
            n: max number of results.
            verdict_filter: if given, only include these verdicts.
                e.g. ["STRONG_BUY", "BUY"] for actionable longs.
            min_confidence: exclude predictions below this threshold.
        """
        preds = list(self._store.values())

        if verdict_filter:
            preds = [p for p in preds if p.verdict in verdict_filter]

        if min_confidence > 0.0:
            preds = [p for p in preds if p.confidence >= min_confidence]

        preds.sort(
            key=lambda p: (_VERDICT_PRIORITY.get(p.verdict, 99), -p.confidence)
        )
        return preds[:n]

    def get_actionable(self, min_confidence: float = 0.55) -> list["TrendPrediction"]:
        """Shortcut: STRONG_BUY + BUY + REDUCE + STRONG_SELL above confidence floor."""
        return self.get_top(
            n=10,
            verdict_filter=["STRONG_BUY", "BUY", "REDUCE", "STRONG_SELL"],
            min_confidence=min_confidence,
        )

    def all(self) -> list["TrendPrediction"]:
        """Return all cached predictions, sorted by priority."""
        return self.get_top(n=len(self._store))

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def is_stale(self, max_age_seconds: int = 14400) -> bool:
        """True if cache is empty or older than max_age_seconds (default 4h)."""
        if self._updated_at is None:
            return True
        age = (datetime.now(UTC) - self._updated_at).total_seconds()
        return age > max_age_seconds

    @property
    def updated_at(self) -> datetime | None:
        return self._updated_at

    @property
    def size(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        age = (
            f"{(datetime.now(UTC) - self._updated_at).seconds // 60}m ago"
            if self._updated_at
            else "never"
        )
        return f"TrendPredictionStore(size={self.size}, updated={age})"
