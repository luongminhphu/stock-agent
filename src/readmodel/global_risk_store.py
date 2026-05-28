"""GlobalRiskStore — in-memory read store for latest IntelligenceEngine verdict.

Owner: readmodel segment.
Purpose: provide zero-latency access to the most recent IE verdict so that
         BriefingService and WatchlistScanService can inject engine context
         without issuing a DB query at call time.

Design:
  - Pure in-memory singleton; no DB, no async I/O.
  - Updated exactly once per IE run by GlobalRiskSubscriber.
  - Thread/task-safe for reads: replaces the entire snapshot atomically.
  - TTL: if last update is older than STALE_HOURS the store self-reports as
    stale so callers can decide whether to trust the cached data.

Usage::

    store = GlobalRiskStore.instance()
    snap  = store.latest()          # GlobalRiskSnapshot | None
    if snap and not store.is_stale():
        flagged = snap.flagged_tickers
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.platform.logging import get_logger

logger = get_logger(__name__)

# Snapshots older than this are considered stale by is_stale().
_STALE_HOURS = 10


@dataclass(frozen=True)
class GlobalRiskSnapshot:
    """Immutable projection of the last IntelligenceEngine verdict."""

    flagged_tickers: list[str]          # tickers IE flagged as high-attention
    risk_level: str                     # e.g. "high" | "medium" | "low"
    market_bias: str                    # e.g. "bearish" | "neutral" | "bullish"
    confidence: float                   # 0.0 – 1.0
    summary: str                        # short narrative from verdict
    action_items: list[str]             # IE-suggested actions
    captured_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    # Optional passthrough fields from EngineVerdict for downstream use
    raw_verdict: dict[str, Any] = field(default_factory=dict)


class GlobalRiskStore:
    """Singleton in-memory store for the latest GlobalRiskSnapshot.

    Call GlobalRiskStore.instance() to obtain the singleton.
    """

    _instance: GlobalRiskStore | None = None

    def __init__(self) -> None:
        self._snapshot: GlobalRiskSnapshot | None = None

    # ── singleton ─────────────────────────────────────────────────────────

    @classmethod
    def instance(cls) -> "GlobalRiskStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── write ─────────────────────────────────────────────────────────────

    def update(self, snapshot: GlobalRiskSnapshot) -> None:
        """Replace the current snapshot.  Called by GlobalRiskSubscriber."""
        self._snapshot = snapshot
        logger.info(
            "global_risk_store.updated",
            risk_level=snapshot.risk_level,
            market_bias=snapshot.market_bias,
            confidence=round(snapshot.confidence, 2),
            flagged_count=len(snapshot.flagged_tickers),
            flagged_tickers=snapshot.flagged_tickers[:10],
        )

    # ── read ──────────────────────────────────────────────────────────────

    def latest(self) -> GlobalRiskSnapshot | None:
        """Return the latest snapshot, or None if never populated."""
        return self._snapshot

    def is_stale(self, stale_hours: int = _STALE_HOURS) -> bool:
        """Return True if the snapshot is absent or older than *stale_hours*."""
        if self._snapshot is None:
            return True
        age = datetime.now(tz=timezone.utc) - self._snapshot.captured_at
        return age > timedelta(hours=stale_hours)

    def flagged_tickers(self) -> list[str]:
        """Convenience: return flagged tickers, or empty list when stale/absent."""
        if self._snapshot is None or self.is_stale():
            return []
        return list(self._snapshot.flagged_tickers)

    def risk_level(self) -> str:
        """Convenience: return risk level string, default 'unknown' when absent."""
        if self._snapshot is None:
            return "unknown"
        return self._snapshot.risk_level
