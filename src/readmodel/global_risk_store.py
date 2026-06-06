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
  GlobalRiskSubscriber   → update(user_id, event)
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

# ---------------------------------------------------------------------------
# DB persistence helpers (Wave D.1)
# ---------------------------------------------------------------------------

async def _persist_risk_snapshot(session_factory, user_id: str, flagged: set, verdict) -> None:
    """Upsert a GlobalRiskSnapshot row. Fire-and-forget — never raises."""
    if session_factory is None:
        return
    try:
        import json as _json
        from datetime import UTC, datetime as _dt
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from src.readmodel.models import GlobalRiskSnapshot

        try:
            verdict_json = _json.dumps(
                verdict.model_dump() if hasattr(verdict, "model_dump") else str(verdict),
                default=str,
            )
        except Exception:
            verdict_json = None

        async with session_factory() as session:
            stmt = pg_insert(GlobalRiskSnapshot).values(
                user_id=user_id,
                flagged_tickers_json=_json.dumps(sorted(flagged)),
                verdict_json=verdict_json,
                updated_at=_dt.now(UTC),
            ).on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "flagged_tickers_json": _json.dumps(sorted(flagged)),
                    "verdict_json": verdict_json,
                    "updated_at": _dt.now(UTC),
                },
            )
            await session.execute(stmt)
            await session.commit()
    except Exception as exc:
        logger.warning("global_risk_store.persist_failed", user_id=user_id, error=str(exc))


async def load_risk_snapshots_from_db(session_factory) -> list[dict]:
    """Load non-stale risk snapshots from DB on startup. Returns list of row dicts."""
    if session_factory is None:
        return []
    try:
        import json as _json
        from datetime import UTC, datetime as _dt, timedelta as _td
        from sqlalchemy import select
        from src.readmodel.models import GlobalRiskSnapshot

        ttl_cutoff = _dt.now(UTC) - _td(hours=_TTL_HOURS)
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(GlobalRiskSnapshot).where(
                        GlobalRiskSnapshot.updated_at > ttl_cutoff
                    )
                )
            ).scalars().all()
            result = []
            for row in rows:
                try:
                    result.append({
                        "user_id": row.user_id,
                        "flagged": set(_json.loads(row.flagged_tickers_json or "[]")),
                        "updated_at": row.updated_at,
                    })
                except Exception:
                    pass
            logger.info("global_risk_store.loaded_from_db", count=len(result))
            return result
    except Exception as exc:
        logger.warning("global_risk_store.load_failed", error=str(exc))
        return []
_TTL = timedelta(hours=_TTL_HOURS)


@dataclass
class _RiskEntry:
    verdict: Any  # EngineVerdict or IntelligenceEngineCompletedEvent — kept as Any to avoid circular import
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_fresh(self) -> bool:
        return (datetime.now(UTC) - self.updated_at) < _TTL


class GlobalRiskStore:
    """In-memory store for the latest verdict/event per user.

    Usage::

        store = get_global_risk_store()
        store.update(user_id, event_or_verdict)
        flagged = store.get_flagged_tickers(user_id)  # set[str]
    """

    def __init__(self, session_factory=None) -> None:
        self._entries: dict[str, _RiskEntry] = {}
        self._session_factory = session_factory

    def update(self, user_id: str, verdict: Any) -> None:
        """Persist a new verdict or IntelligenceEngineCompletedEvent for user_id."""
        self._entries[user_id] = _RiskEntry(verdict=verdict)
        flagged = self._extract_flagged(verdict)
        logger.info(
            "global_risk_store.updated",
            user_id=user_id,
            flagged_count=len(flagged),
            flagged_tickers=sorted(flagged),
        )
        # Wave D.1: fire-and-forget persist to DB
        import asyncio as _asyncio
        _asyncio.create_task(
            _persist_risk_snapshot(self._session_factory, user_id, flagged, verdict)
        )

    def get_flagged_tickers(self, user_id: str) -> set[str]:
        """Return set of tickers flagged as high-risk in the latest fresh verdict.

        Returns empty set when:
        - No verdict stored for user_id.
        - Verdict is stale (older than TTL of 4h).
        - Verdict has no recognised ticker field.
        """
        entry = self._entries.get(user_id)
        if entry is None or not entry.is_fresh():
            return set()
        return self._extract_flagged(entry.verdict)

    def get_verdict(self, user_id: str) -> Any | None:
        """Return raw stored object if fresh, else None."""
        entry = self._entries.get(user_id)
        if entry is None or not entry.is_fresh():
            return None
        return entry.verdict

    def clear(self, user_id: str) -> None:
        """Remove stored entry for user_id (useful in tests)."""
        self._entries.pop(user_id, None)

    @staticmethod
    def _extract_flagged(verdict: Any) -> set[str]:
        """Extract flagged ticker set from an EngineVerdict or IntelligenceEngineCompletedEvent.

        Resolution order:
        1. flagged_tickers: tuple[str, ...]  — set by engine.py from snapshot (primary, Option C)
        2. risk_tickers: list[str]           — legacy EngineVerdict field name
        3. flagged_tickers: list[str]        — legacy fallback
        4. top_signals: list with .ticker    — legacy fallback

        Returns empty set when none found or verdict is None.
        """
        if verdict is None:
            return set()

        # Primary: flagged_tickers tuple (IntelligenceEngineCompletedEvent, Option C)
        flagged = getattr(verdict, "flagged_tickers", None)
        if flagged:
            return {t.upper() for t in flagged if t}

        # Legacy: risk_tickers list (EngineVerdict direct)
        risk = getattr(verdict, "risk_tickers", None)
        if risk:
            return {t.upper() for t in risk if t}

        # Legacy: top_signals with ticker attr
        signals = getattr(verdict, "top_signals", None)
        if signals:
            return {s.ticker.upper() for s in signals if getattr(s, "ticker", None)}

        return set()


    async def warm_load(self) -> int:
        """Load non-stale risk snapshots from DB into memory on startup.

        Returns number of users loaded.
        Prevents BriefingService/ScanService from seeing empty flagged_tickers
        after a restart when an engine cycle hasn't run yet.
        """
        rows = await load_risk_snapshots_from_db(self._session_factory)
        for row in rows:
            user_id = row["user_id"]
            dummy_verdict = _DummyVerdict(row["flagged"])
            self._entries[user_id] = _RiskEntry(
                verdict=dummy_verdict,
                updated_at=row.get("updated_at"),
            )
        return len(rows)


# Module-level singleton
_instance: GlobalRiskStore | None = None


class _DummyVerdict:
    """Minimal verdict stub for warm-loaded GlobalRiskStore entries.
    Allows get_flagged_tickers() to extract tickers without a full schema restore.
    """
    def __init__(self, flagged: set[str]) -> None:
        self.flagged_tickers = list(flagged)
        self.risk_tickers = list(flagged)


def get_global_risk_store() -> GlobalRiskStore:
    """Return the module-level GlobalRiskStore singleton."""
    global _instance
    if _instance is None:
        _instance = GlobalRiskStore()
    return _instance
