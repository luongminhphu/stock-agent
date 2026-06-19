"""IntelligenceReadService — read intelligence snapshot from in-process store.

Owner: readmodel segment.

Extracted from DashboardService to keep DashboardService as a thin facade.
All logic formerly in DashboardService.get_intelligence() lives here.

Data source: IntelligenceSnapshotStore (in-process, no DB, no AI call).
The store is populated by IntelligenceSnapshotSubscriber listening to
IntelligenceEngineCompletedEvent — wired in bootstrap.py.

Cached 30s per user_id.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.readmodel.cache import DashboardTTLCache

logger = get_logger(__name__)

# Module-level cache — shared across all IntelligenceReadService instances.
_cache = DashboardTTLCache()

# ---------------------------------------------------------------------------
# Intelligence snapshot store import (guarded)
# ---------------------------------------------------------------------------

try:
    from src.readmodel.intelligence_snapshot import get_intelligence_snapshot

    _INTELLIGENCE_SNAPSHOT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _INTELLIGENCE_SNAPSHOT_AVAILABLE = False


class IntelligenceReadService:
    """Read intelligence snapshot from in-process store and serialize to dict.

    Returns None when:
      - store not yet populated (engine hasn't run today)
      - bootstrap() hasn't been called (ImportError guard)
      - store raises unexpectedly
    """

    def __init__(self, session: AsyncSession | None = None) -> None:
        pass  # session param kept for DI consistency; not used

    async def get_intelligence(self, user_id: str) -> dict[str, Any] | None:
        """Return the latest intelligence snapshot for user, or None.

        Output shape:
            overall_verdict:   str | None
            confidence:        float | None
            market_context:    str | None
            priority_actions:  list[dict]   [{ticker, action_text, urgency, reasoning}]
            risk_flags:        list[dict]   [{description, severity}]
            watch_list:        list[str]
            is_stale:          bool
            generated_at:      str | None   ISO8601 UTC
        """
        cached = _cache.get("intelligence", user_id)
        if cached is not None:
            return cached

        if not _INTELLIGENCE_SNAPSHOT_AVAILABLE:
            logger.warning(
                "get_intelligence.unavailable",
                user_id=user_id,
                reason="intelligence_snapshot module not importable",
            )
            return None

        try:
            store = get_intelligence_snapshot()
            snap_result = await store.get(user_id)
            if snap_result is None:
                logger.debug("get_intelligence.no_snapshot", user_id=user_id)
                return None

            report, is_stale = snap_result
            generated_at = store.last_updated_at(user_id)

            def _serialize_actions(actions: list | None) -> list[dict]:
                if not actions:
                    return []
                return [
                    {
                        "ticker": str(getattr(a, "ticker", "") or "").upper() or None,
                        "action_text": str(getattr(a, "action_text", "") or "")[:300],
                        "urgency": str(getattr(a, "urgency", "medium") or "medium").lower(),
                        "reasoning": str(getattr(a, "reasoning", "") or "")[:500] or None,
                    }
                    for a in actions
                ]

            def _serialize_risk_flags(flags: list | None) -> list[dict]:
                if not flags:
                    return []
                return [
                    {
                        "description": str(getattr(f, "description", "") or "")[:300],
                        "severity": str(getattr(f, "severity", "low") or "low").lower(),
                    }
                    for f in flags
                ]

            result: dict[str, Any] = {
                "overall_verdict": str(getattr(report, "overall_verdict", "") or "") or None,
                # top_verdict is the canonical field (IntelligenceReport uses top_verdict)
                "top_verdict": str(
                    getattr(report, "top_verdict", "")
                    or getattr(report, "overall_verdict", "")
                    or ""
                ) or None,
                "conviction": str(
                    getattr(report, "top_verdict_conviction", "")
                    or getattr(report, "conviction", "medium")
                    or "medium"
                ).lower(),
                "confidence": float(
                    getattr(report, "overall_confidence", None)
                    or getattr(report, "confidence", 0.0)
                    or 0.0
                ),
                "narrative_summary": str(
                    getattr(report, "narrative_summary", "")
                    or getattr(report, "market_context", "")
                    or ""
                )[:800] or None,
                "market_context": str(getattr(report, "market_context", "") or "")[:500] or None,
                "priority_actions": _serialize_actions(getattr(report, "priority_actions", None)),
                "risk_flags": _serialize_risk_flags(getattr(report, "risk_flags", None)),
                "watch_list": [
                    str(t).upper()
                    for t in (
                        getattr(report, "next_watch_tickers", None)
                        or getattr(report, "watch_list", None)
                        or []
                    )
                    if t
                ],
                "is_stale": is_stale,
                "generated_at": generated_at.isoformat() if generated_at else None,
            }

            _cache.set("intelligence", user_id, result)
            logger.debug(
                "get_intelligence.ok",
                user_id=user_id,
                is_stale=is_stale,
                priority_actions=len(result["priority_actions"]),
                risk_flags=len(result["risk_flags"]),
            )
            return result

        except Exception as exc:
            logger.warning(
                "get_intelligence.error",
                user_id=user_id,
                error=str(exc),
                exc_info=True,
            )
            return None
