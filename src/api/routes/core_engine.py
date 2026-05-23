"""Core Intelligence Engine API routes.

Owner: api segment — thin adapter only.
Delegates 100% to src.core.engine and src.core.snapshot.
No business logic here.

Endpoints:
    POST /api/v1/core/engine/run   — run a full cycle: snapshot → verdict → dispatch
    GET  /api/v1/core/snapshot     — raw SystemSnapshot (no AI synthesis)
    POST /api/v1/core/feedback     — submit verdict outcome for self-improvement loop
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db
from src.core.engine import IntelligenceEngine
from src.core.feedback import FeedbackStore
from src.core.schemas import EngineOutput, FeedbackEntry, SystemSnapshot
from src.core.snapshot import SystemSnapshotBuilder
from src.platform.config import settings

router = APIRouter(prefix="/core", tags=["core-engine"])


def _default_user_id() -> str:
    if not settings.owner_user_id:
        raise HTTPException(
            status_code=500,
            detail="owner_user_id is not configured. Set it in .env for single-user mode.",
        )
    return settings.owner_user_id


# ---------------------------------------------------------------------------
# 1. Run full engine cycle
# ---------------------------------------------------------------------------


@router.post("/engine/run", response_model=EngineOutput)
async def run_engine_cycle(
    session: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[
        str | None,
        Query(description="User ID. Mặc định dùng owner_user_id từ .env (single-user mode).")
    ] = None,
) -> EngineOutput:
    """Chạy một vòng Intelligence Engine.

    Flow: SystemSnapshot → signal ranking → EngineVerdict → dispatch.

    Wave 1: rule-based synthesis, dispatch chỉ log.
    Wave 2: synthesis qua AIClient.
    Wave 3: dispatch thật sang briefing + bot.
    """
    uid = user_id or _default_user_id()
    engine = IntelligenceEngine(session=session, user_id=uid)
    return await engine.run_cycle()


# ---------------------------------------------------------------------------
# 2. Raw snapshot (no synthesis)
# ---------------------------------------------------------------------------


@router.get("/snapshot", response_model=SystemSnapshot)
async def get_system_snapshot(
    session: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[
        str | None,
        Query(description="User ID. Mặc định dùng owner_user_id từ .env.")
    ] = None,
) -> SystemSnapshot:
    """Thu thập SystemSnapshot cross-segment mà không chạy AI synthesis.

    Hữu ích để debug trạng thái hệ thống hoặc feed vào external tool.
    Sources: watchlist alerts, overdue thesis reviews, market scan, portfolio.
    """
    uid = user_id or _default_user_id()
    return await SystemSnapshotBuilder(session=session, user_id=uid).build()


# ---------------------------------------------------------------------------
# 3. Feedback submission
# ---------------------------------------------------------------------------


@router.post("/feedback")
async def submit_feedback(
    entry: FeedbackEntry,
    session: Annotated[AsyncSession, Depends(get_db)],  # noqa: ARG001 — Wave 3 will use this
) -> dict:
    """Ghi nhận outcome của một verdict.

    Được dùng bởi self-improvement loop:
    - Wave 3: persist to DB, reweight signal scores.
    - Wave 4: evolution.py phân tích patterns và đề xuất cải tiến prompt/rule.

    Wave 1: in-memory log, confirm receipt.
    """
    await FeedbackStore.record(entry)
    return {
        "status": "received",
        "verdict_id": entry.verdict_id,
        "outcome": entry.outcome,
        "note": "Wave 1 — stored in-memory. Wave 3 sẽ persist vào DB và reweight signals.",
    }
