"""Briefing routes.

Owner: api segment.
Thin adapters over BriefingService and DashboardService only.
No business logic lives here.

Endpoints:
  GET  /briefing/latest?phase={morning|eod}  — read snapshot, 0 AI call (dashboard)
  GET  /briefing/feedback-summary            — feedback stats (dashboard)
  POST /briefing/{phase}/generate            — explicit AI trigger (user intent)
  GET  /briefing/morning                     — backward compat (bot / scheduler)
  GET  /briefing/eod                         — backward compat (bot / scheduler)
  POST /briefing/{snapshot_id}/feedback      — record feedback, append-only
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_briefing_service, get_current_user_id, get_db
from src.api.dto.briefing import BriefResponse, FeedbackRequest

router = APIRouter(prefix="/briefing", tags=["briefing"])

_VALID_PHASES = frozenset({"morning", "eod"})


# ── READ — dashboard (no AI) ───────────────────────────────────────────────────

@router.get("/latest", response_model=BriefResponse)
async def get_latest_brief(
    phase: str = Query(..., description="morning | eod"),
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> BriefResponse:
    """Return latest persisted brief snapshot. Never calls AI.

    Used by dashboard-loader.js on page load.
    Returns 404 if no snapshot exists yet for this phase.
    """
    if phase not in _VALID_PHASES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="phase phải là 'morning' hoặc 'eod'",
        )
    from src.readmodel.dashboard_service import DashboardService

    data = await DashboardService(session).get_brief_latest(user_id=user_id, phase=phase)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chưa có {phase} brief. Hãy bấm 'Tạo Brief'.",
        )
    return BriefResponse(**{k: v for k, v in data.items() if k != "content"})


@router.get("/feedback-summary")
async def get_brief_feedback_summary(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return brief feedback summary (acted_rate, counts).

    Used by dashboard-loader.js on page load.
    Always returns a valid dict — never raises.
    """
    from src.readmodel.dashboard_service import DashboardService

    return await DashboardService(session).get_brief_feedback_summary(user_id=user_id)


# ── GENERATE — explicit AI trigger ────────────────────────────────────────────

@router.post("/{phase}/generate", response_model=BriefResponse)
async def generate_brief(
    phase: str,
    user_id: str = Depends(get_current_user_id),
    briefing_svc=Depends(get_briefing_service),
) -> BriefResponse:
    """Trigger AI brief generation and persist snapshot.

    Must be called only on explicit user intent (button click).
    Not for use by scheduler or background jobs — those call service directly.
    """
    if phase not in _VALID_PHASES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="phase phải là 'morning' hoặc 'eod'",
        )
    try:
        if phase == "morning":
            brief = await briefing_svc.generate_morning_brief(user_id=user_id)
        else:
            brief = await briefing_svc.generate_eod_brief(user_id=user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{phase} brief generation failed: {exc}",
        ) from exc
    return BriefResponse(snapshot_id=brief.snapshot_id, **brief.output.model_dump())


# ── BACKWARD COMPAT — bot / scheduler ─────────────────────────────────────────

@router.get("/morning", response_model=BriefResponse)
async def get_morning_brief(
    user_id: str = Depends(get_current_user_id),
    briefing_svc=Depends(get_briefing_service),
) -> BriefResponse:
    """Generate + return morning brief. Kept for bot adapters."""
    try:
        brief = await briefing_svc.generate_morning_brief(user_id=user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Morning brief failed: {exc}",
        ) from exc
    return BriefResponse(snapshot_id=brief.snapshot_id, **brief.output.model_dump())


@router.get("/eod", response_model=BriefResponse)
async def get_eod_brief(
    user_id: str = Depends(get_current_user_id),
    briefing_svc=Depends(get_briefing_service),
) -> BriefResponse:
    """Generate + return EOD brief. Kept for bot adapters."""
    try:
        brief = await briefing_svc.generate_eod_brief(user_id=user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"EOD brief failed: {exc}",
        ) from exc
    return BriefResponse(snapshot_id=brief.snapshot_id, **brief.output.model_dump())


# ── FEEDBACK ──────────────────────────────────────────────────────────────────

@router.post("/{snapshot_id}/feedback", status_code=204)
async def post_brief_feedback(
    snapshot_id: int,
    body: FeedbackRequest,
    user_id: str = Depends(get_current_user_id),
    briefing_svc=Depends(get_briefing_service),
) -> None:
    """Record user feedback for a brief snapshot.

    outcome: "acted" | "watching" | "skipped"
    Append-only — does not overwrite previous rows.
    Always returns 204 (errors are swallowed in service layer).
    """
    await briefing_svc.record_feedback(
        brief_snapshot_id=snapshot_id,
        user_id=user_id,
        outcome=body.outcome,
    )
