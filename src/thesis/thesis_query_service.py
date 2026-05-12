"""ThesisQueryService — read-model adapter for SignalEngineListener.

Owner: thesis segment.
Consumer: ai.signal_engine_listener (read-only).

Exposes get_active_with_components(user_id) — returns active theses with
assumptions + catalysts as dicts, ready for AI agent injection.

Pattern: session_factory injection, never raises — returns [] on error.
"""
from __future__ import annotations

from typing import Any

from src.platform.logging import get_logger

logger = get_logger(__name__)


class ThesisQueryService:
    """Read-only: fetch active theses with assumptions + catalysts."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def get_active_with_components(
        self, user_id: str
    ) -> list[dict[str, Any]]:
        """Return active thesis dicts including assumptions and catalysts.

        Uses ThesisRepository.list_active_for_user() which eager-loads
        assumptions + catalysts in a single query.

        Returns [] on any error.
        """
        try:
            async with self._session_factory() as session:
                from src.thesis.repository import ThesisRepository

                repo = ThesisRepository(session)
                theses = await repo.list_active_for_user(user_id=user_id)

                return [self._to_dict(t) for t in theses]
        except Exception as exc:
            logger.warning(
                "thesis_query_service.get_active_with_components_failed",
                user_id=user_id,
                error=str(exc),
            )
            return []

    def _to_dict(self, thesis: Any) -> dict[str, Any]:
        assumptions = getattr(thesis, "assumptions", []) or []
        catalysts = getattr(thesis, "catalysts", []) or []

        return {
            "thesis_id": str(thesis.id),
            "ticker": thesis.ticker,
            "title": thesis.title,
            "summary": getattr(thesis, "summary", "") or "",
            "direction": str(getattr(thesis, "direction", "LONG") or "LONG").upper(),
            "status": str(getattr(thesis, "status", "active")),
            "entry_price": getattr(thesis, "entry_price", None),
            "target_price": getattr(thesis, "target_price", None),
            "stop_loss": getattr(thesis, "stop_loss", None),
            "created_at": (
                thesis.created_at.isoformat()
                if getattr(thesis, "created_at", None)
                else None
            ),
            "assumptions": [
                {
                    "id": a.id,
                    "description": getattr(a, "description", ""),
                    "status": str(getattr(a, "status", "valid")),
                    "note": getattr(a, "note", "") or "",
                }
                for a in assumptions
            ],
            "catalysts": [
                {
                    "id": c.id,
                    "description": getattr(c, "description", ""),
                    "status": str(getattr(c, "status", "pending")),
                    "expected_date": (
                        c.expected_date.isoformat()
                        if getattr(c, "expected_date", None)
                        else None
                    ),
                }
                for c in catalysts
            ],
        }
