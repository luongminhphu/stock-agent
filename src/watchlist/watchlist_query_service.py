"""WatchlistQueryService — read-model adapter for AI listeners.

Owner: watchlist segment.
Consumer: ai.signal_engine_listener, ai.trend_engine_listener (read-only).

Exposes get_latest_outputs(user_id) which wraps build_thesis_health_snapshots()
— the canonical thesis health read-path already used by ai.context_builder.

Pattern: session_factory injection. One AsyncSession per call, closed on exit.
Never raises — returns [] on any error so listeners degrade gracefully.

Note: previously located at src/thesis/watchlist_query_service.py.
Moved here because health snapshot reads are a watchlist segment concern,
not thesis. src/thesis/watchlist_query_service.py now re-exports this class
as a backward-compat shim.
"""
from __future__ import annotations

from typing import Any

from src.platform.logging import get_logger

logger = get_logger(__name__)


class WatchlistQueryService:
    """Read-only: fetch latest thesis health snapshots for AI listeners."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def get_latest_outputs(self, user_id: str) -> list[dict[str, Any]]:
        """Return list of ThesisHealthSnapshot dicts for all active theses.

        Uses build_thesis_health_snapshots() — same path as context_builder.
        Returns [] on any error.
        """
        try:
            async with self._session_factory() as session:
                from src.thesis.health_snapshot import build_thesis_health_snapshots

                snapshots = await build_thesis_health_snapshots(
                    session=session,
                    user_id=user_id,
                )
                return [
                    {
                        "thesis_id": s.thesis_id,
                        "ticker": s.ticker,
                        "title": s.title,
                        "direction": s.direction,
                        "health_score": s.health_score,
                        "days_since_review": s.days_since_review,
                        "distance_to_stop_pct": s.distance_to_stop_pct,
                        "assumptions_total": s.assumptions_total,
                        "assumptions_invalidated": s.assumptions_invalidated,
                        "last_verdict": s.last_verdict,
                        "urgency_flag": s.urgency_flag,
                        "stop_loss": s.stop_loss,
                        "target_price": s.target_price,
                        "prompt_line": s.format_for_prompt(),
                    }
                    for s in snapshots
                ]
        except Exception as exc:
            logger.warning(
                "watchlist_query_service.get_latest_outputs_failed",
                user_id=user_id,
                error=str(exc),
            )
            return []
