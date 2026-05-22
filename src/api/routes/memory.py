"""Memory routes.

Owner: api segment.
Thin adapters over MemoryService, MemorySnapshotRepository, MemoryConsolidator.
No business logic lives here.

Endpoints:
  GET  /memory/snapshot  — read latest MemorySnapshot + MemoryContext (no AI)
  POST /memory/refresh   — trigger on-demand pattern synthesis (AI call)
                           Returns same shape as GET /snapshot so callers can
                           update UI directly without a follow-up refetch.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_db

router = APIRouter(prefix="/memory", tags=["memory"])


# ── READ — no AI ──────────────────────────────────────────────────────────────

@router.get("/snapshot")
async def get_memory_snapshot(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return latest persisted MemorySnapshot + MemoryContext.

    Never calls AI. Used by dashboard Memory panel on load.
    Returns 404 when no snapshot exists yet.
    Returns MemoryContext fields even when snapshot is absent (episodes may exist).
    """
    from src.ai.memory.memory_service import MemoryService
    from src.ai.memory.repository import MemorySnapshotRepository

    mem_ctx = await MemoryService.get_memory_context(session, user_id=user_id)

    snapshot_repo = MemorySnapshotRepository(session)
    snapshot = await snapshot_repo.get_latest(user_id=user_id)

    if mem_ctx.is_empty() and snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No memory data yet. Use the system to accumulate episodes.",
        )

    return _build_snapshot_response(snapshot)


# ── REFRESH — explicit AI trigger ─────────────────────────────────────────────

@router.post("/refresh")
async def refresh_memory(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger on-demand pattern synthesis and persist snapshot.

    Calls AI. Only on explicit user intent (button click).
    Returns {status: 'insufficient_data'} with HTTP 202 when < 5 episodes.

    On success returns the SAME shape as GET /snapshot so the caller can update
    the UI in a single round-trip — no follow-up GET needed.
    """
    from src.ai.client import AIClient
    from src.ai.memory.consolidator import MemoryConsolidator
    from src.ai.memory.repository import MemorySnapshotRepository

    try:
        consolidator = MemoryConsolidator(client=AIClient(), user_id=user_id)
        output = await consolidator.synthesize_patterns(session)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Pattern synthesis failed: {exc}",
        ) from exc

    if output is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "insufficient_data",
                "detail": "Cần tối thiểu 5 AI interactions trong 14 ngày gần nhất.",
            },
        )

    # Reload the just-persisted snapshot so we can return episode_count + period_end
    # (consolidator already wrote it to DB within synthesize_patterns)
    snapshot_repo = MemorySnapshotRepository(session)
    snapshot = await snapshot_repo.get_latest(user_id=user_id)

    response = _build_snapshot_response(snapshot)
    response["status"] = "ok"
    return response


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_snapshot_response(snapshot: object | None) -> dict:
    """Serialise a MemorySnapshot ORM row into the canonical snapshot dict.

    Shared by GET /snapshot and POST /refresh so both endpoints always return
    identical shape. Callers can rely on the same keys regardless of which
    endpoint they hit.
    """
    import json as _json

    patterns: list[str] = []
    bias_warnings: list[str] = []
    market_regime_reads: list[str] = []
    confidence: float = 0.0
    period_end: str | None = None
    episode_count: int = 0

    if snapshot is not None:
        episode_count = getattr(snapshot, "episode_count", 0) or 0
        raw_period_end = getattr(snapshot, "period_end", None)
        if raw_period_end:
            period_end = raw_period_end.strftime("%d/%m/%Y %H:%M")

        behavioral = getattr(snapshot, "behavioral_patterns", None)
        if behavioral:
            try:
                from src.ai.memory.consolidator import PatternSynthesisOutput
                stored = _json.loads(behavioral) if isinstance(behavioral, str) else behavioral
                synth = PatternSynthesisOutput(**stored)
                patterns = synth.patterns
                bias_warnings = synth.bias_warnings
                market_regime_reads = synth.market_regime_reads
                confidence = synth.confidence
            except Exception:
                pass

    return {
        "has_snapshot": snapshot is not None,
        "episode_count": episode_count,
        "confidence": confidence,
        "period_end": period_end,
        "patterns": patterns,
        "bias_warnings": bias_warnings,
        "market_regime_reads": market_regime_reads,
    }
