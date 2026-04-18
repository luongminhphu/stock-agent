"""Briefing routes.

Owner: api segment.
Thin adapters over BriefingService only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_briefing_service, get_current_user_id
from src.api.dto.briefing import BriefResponse

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
    return BriefResponse.model_validate(brief.model_dump())


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
            detail=f"Morning brief failed: {exc}",
        ) from exc
    return BriefResponse.model_validate(brief.model_dump())
