"""Briefing routes.

Owner: api segment.
Thin adapters over BriefingService only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_briefing_service, get_current_user_id
from src.api.dto.briefing import BriefResponse, FeedbackRequest

router = APIRouter(prefix="/briefing", tags=["briefing"])


@router.get("/morning", response_model=BriefResponse)
async def get_morning_brief(
    user_id: str = Depends(get_current_user_id),
    briefing_svc=Depends(get_briefing_service),
) -> BriefResponse:
    try:
        brief = await briefing_svc.generate_morning_brief(user_id=user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Morning brief failed: {exc}",
        ) from exc
    return BriefResponse(
        snapshot_id=brief.snapshot_id,
        **brief.output.model_dump(),
    )


@router.get("/eod", response_model=BriefResponse)
async def get_eod_brief(
    user_id: str = Depends(get_current_user_id),
    briefing_svc=Depends(get_briefing_service),
) -> BriefResponse:
    try:
        brief = await briefing_svc.generate_eod_brief(user_id=user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"EOD brief failed: {exc}",
        ) from exc
    return BriefResponse(
        snapshot_id=brief.snapshot_id,
        **brief.output.model_dump(),
    )


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
