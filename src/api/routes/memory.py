"""Memory routes.

Owner: api segment.
Thin adapters over MemoryService, MemorySnapshotRepository, MemoryConsolidator.
No business logic lives here.

Endpoints:
  GET  /memory/snapshot  — read latest MemorySnapshot + MemoryContext (no AI)
  POST /memory/refresh   — trigger on-demand pattern synthesis (AI call)
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_db

router = APIRouter(prefix="/memory", tags=["memory"])


# ── READ — no AI ──────────────────────────────────────────────────────────────────────

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

    # MemoryContext — built from episodic store (fast, no AI)
    mem_ctx = await MemoryService.get_memory_context(session, user_id=user_id)

    # Latest persisted snapshot (may be None on first run)
    snapshot_repo = MemorySnapshotRepository(session)
    snapshot = await snapshot_repo.get_latest(user_id=user_id)

    if mem_ctx.is_empty() and snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No memory data yet. Use the system to accumulate episodes.",
        )

    # Deserialise stored pattern blob if present
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
                stored = json.loads(behavioral) if isinstance(behavioral, str) else behavioral
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
        "context_summary": mem_ctx.render() if not mem_ctx.is_empty() else None,
    }


# ── REFRESH — explicit AI trigger ─────────────────────────────────────────────────────────

@router.post("/refresh")
async def refresh_memory(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger on-demand pattern synthesis and persist snapshot.

    Calls AI. Only on explicit user intent (button click).
    Returns {status: 'insufficient_data'} with HTTP 202 when < 5 episodes.
    Returns synthesised PatternSynthesisOutput on success.
    """
    from src.ai.client import AIClient
    from src.ai.memory.consolidator import MemoryConsolidator

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

    return {
        "status": "ok",
        "confidence": output.confidence,
        "patterns": output.patterns,
        "bias_warnings": output.bias_warnings,
        "market_regime_reads": output.market_regime_reads,
    }
