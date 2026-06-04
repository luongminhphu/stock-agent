"""ThesisRiskSignalQuery — derived risk signals for AI signal engine.

Owner: thesis segment.
Consumer: ai.signal_engine_listener (read-only).

Derive stress-test-style risk signals from current thesis assumption state.
This is NOT a query adapter for persisted StressTest outputs — StressTestService
does not persist results to DB. Risk signals are built on-the-fly from:
    - threatened assumptions (INVALID / UNCERTAIN)
    - invalidation_probability = threatened / total
    - pending catalysts

Outputs are tagged with _source: 'derived_from_thesis_state' so downstream
SignalEngineAgent knows the provenance and can weight accordingly.

If true last-run persistence is ever needed, introduce a StressTestResult
DB table and graduate this class into a real readmodel projection.

Pattern: session_factory injection, never raises — returns [] on error.

Naming note: previously StressTestQueryService. Renamed to ThesisRiskSignalQuery
to avoid implying persisted StressTest entity queries exist.
"""
from __future__ import annotations

from typing import Any

from src.platform.logging import get_logger

logger = get_logger(__name__)


class ThesisRiskSignalQuery:
    """Read-only: derive stress-test risk signals from active thesis state."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def get_latest_outputs(self, user_id: str) -> list[dict[str, Any]]:
        """Return risk signal dicts derived from active thesis assumptions.

        Since StressTestService does not persist outputs, we build a best-effort
        risk picture from current thesis state:
        - threatened_assumptions: INVALID or UNCERTAIN assumptions
        - invalidation_probability: ratio of threatened / total assumptions
        - verdict: derived from ratio thresholds

        Returns [] on any error.
        """
        try:
            async with self._session_factory() as session:
                from src.thesis.repository import ThesisRepository
                from src.thesis.models import AssumptionStatus

                repo = ThesisRepository(session)
                theses = await repo.list_active_for_user(user_id=user_id)

                results: list[dict[str, Any]] = []
                for thesis in theses:
                    assumptions = getattr(thesis, "assumptions", []) or []
                    total = len(assumptions)

                    threatened = [
                        a for a in assumptions
                        if getattr(a, "status", None) in (
                            AssumptionStatus.INVALID,
                            AssumptionStatus.UNCERTAIN,
                        )
                    ]
                    broken = [
                        a for a in assumptions
                        if getattr(a, "status", None) == AssumptionStatus.INVALID
                    ]

                    invalidation_prob = len(threatened) / total if total > 0 else 0.0

                    if invalidation_prob >= 0.6:
                        verdict = "INVALIDATED"
                    elif invalidation_prob >= 0.3:
                        verdict = "WEAKENING"
                    else:
                        verdict = "VALID"

                    catalysts = getattr(thesis, "catalysts", []) or []
                    pending_catalysts = [
                        c.description
                        for c in catalysts
                        if str(getattr(c, "status", "pending")).lower() == "pending"
                    ]

                    results.append({
                        "thesis_id": str(thesis.id),
                        "ticker": thesis.ticker,
                        "thesis_title": thesis.title,
                        "verdict": verdict,
                        "invalidation_probability": round(invalidation_prob, 3),
                        "broken_assumption_count": len(broken),
                        "weakened_assumption_count": len(threatened) - len(broken),
                        "threatened_assumptions": [
                            {
                                "description": getattr(a, "description", ""),
                                "status": str(getattr(a, "status", "")),
                                "threat_level": (
                                    "BROKEN"
                                    if getattr(a, "status", None) == AssumptionStatus.INVALID
                                    else "WEAKENED"
                                ),
                            }
                            for a in threatened
                        ],
                        "pending_catalysts": pending_catalysts,
                        "_source": "derived_from_thesis_state",
                    })

                return results
        except Exception as exc:
            logger.warning(
                "thesis_risk_signal_query.get_latest_outputs_failed",
                user_id=user_id,
                error=str(exc),
            )
            return []
