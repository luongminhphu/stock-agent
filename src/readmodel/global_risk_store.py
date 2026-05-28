"""GlobalRiskStore — in-memory read model for latest EngineVerdict per user.

Owner: readmodel segment.

Stores the most-recent IntelligenceEngine verdict per user_id so that
downstream capabilities (briefing, thesis, watchlist) can read flagged
tickers without a DB round-trip.

Design:
- Singleton via module-level _store dict — no external dependency.
- TTL: 4h. Entries older than TTL are treated as absent (safe default).
- get_flagged_tickers() returns set[str] — empty set when no fresh verdict.
- Thread-safe via asyncio single-threaded assumption (no lock needed for
  standard asyncio event loop).

Interface consumed by:
  GlobalRiskSubscriber   → update(user_id, verdict)
  BriefingService        → get_flagged_tickers(user_id)   (Commit 3)
  ScanService            → get_flagged_tickers(user_id)   (Commit 4)
  ThesisReviewService    → get_flagged_tickers(user_id)   (Commit 5)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from src.platform.logging import get_logger

logger = get_logger(__name__)

_TTL_HOURS = 4
_TTL = timedelta(hours=_TTL_HOURS)


@dataclass
class _RiskEntry:
    verdict: Any  # EngineVerdict — kept as Any to avoid circular import
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_fresh(self) -> bool:
        return (datetime.now(UTC) - self.updated_at) < _TTL


class GlobalRiskStore:
    """In-memory store for the latest EngineVerdict per user.

    Usage::

        store = get_global_risk_store()
        store.update(user_id, verdict)
        flagged = store.get_flagged_tickers(user_id)  # set[str]
    """

    def __init__(self) -> None:
        self._entries: dict[str, _RiskEntry] = {}

    def update(self, user_id: str, verdict: Any) -> None:
        """Persist a new EngineVerdict for user_id, replacing any prior entry."""
        self._entries[user_id] = _RiskEntry(verdict=verdict)
        flagged = self._extract_flagged(verdict)
        logger.info(
            "global_risk_store.updated",
            user_id=user_id,
            flagged_count=len(flagged),
            flagged_tickers=sorted(flagged),
        )

    def get_flagged_tickers(self, user_id: str) -> set[str]:
        """Return set of tickers flagged as high-risk in the latest fresh verdict.

        Returns empty set when:
        - No verdict stored for user_id.
        - Verdict is stale (older than TTL of 4h).
        - Verdict has no risk_tickers / flagged_tickers field.
        """
        entry = self._entries.get(user_id)
        if entry is None or not entry.is_fresh():
            return set()
        return self._extract_flagged(entry.verdict)

    def get_verdict(self, user_id: str) -> Any | None:
        """Return raw EngineVerdict if fresh, else None."""
        entry = self._entries.get(user_id)
        if entry is None or not entry.is_fresh():
            return None
        return entry.verdict

    def clear(self, user_id: str) -> None:
        """Remove stored entry for user_id (useful in tests)."""
        self._entries.pop(user_id, None)

    @staticmethod
    def _extract_flagged(verdict: Any) -> set[str]:
        """Extract flagged ticker set from an EngineVerdict.

        Supports two common field names used by EngineVerdict:
        - risk_tickers: list[str]  (primary)
        - flagged_tickers: list[str]  (fallback)
        - top_signals: list with .ticker attr  (fallback)

        Returns empty set when none found or verdict is None.
        """
        if verdict is None:
            return set()
        # Primary: explicit risk_tickers field
        risk = getattr(verdict, "risk_tickers", None)
        if risk:
            return {t.upper() for t in risk if t}
        # Fallback: flagged_tickers
        flagged = getattr(verdict, "flagged_tickers", None)
        if flagged:
            return {t.upper() for t in flagged if t}
        # Fallback: top_signals with ticker attr
        signals = getattr(verdict, "top_signals", None)
        if signals:
            return {s.ticker.upper() for s in signals if getattr(s, "ticker", None)}
        return set()


# Module-level singleton
_instance: GlobalRiskStore | None = None


def get_global_risk_store() -> GlobalRiskStore:
    """Return the module-level GlobalRiskStore singleton."""
    global _instance
    if _instance is None:
        _instance = GlobalRiskStore()
    return _instance
