"""Thesis routes.

Owner: api segment.
Exposes thesis CRUD + list via ThesisService.
No business logic — parsing and response shaping only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db, get_current_user_id
from src.api.dto.thesis import (
    CreateThesisRequest,
    ThesisResponse,
    ThesisListResponse,
)
from src.thesis.service import (
    CreateThesisInput,
    ThesisNotFoundError,
    ThesisAlreadyClosedError,
    ThesisService,
)
from src.thesis.models import ThesisStatus

router = APIRouter(prefix="/thesis", tags=["thesis"])


@router.post("", response_model=ThesisResponse, status_code=status.HTTP_201_CREATED)
async def create_thesis(
    body: CreateThesisRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> ThesisResponse:
    svc = ThesisService(db)
    thesis = await svc.create(CreateThesisInput(
        user_id=user_id,
        ticker=body.ticker.upper(),
        title=body.title,
        summary=body.summary,
        entry_price=body.entry_price,
        target_price=body.target_price,
        stop_loss=body.stop_loss,
    ))
    return ThesisResponse.model_validate(thesis)


@router.get("", response_model=ThesisListResponse)
async def list_theses(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> ThesisListResponse:
    svc = ThesisService(db)
    theses = await svc.list_for_user(user_id, status=ThesisStatus.ACTIVE)
    return ThesisListResponse(items=[ThesisResponse.model_validate(t) for t in theses])


@router.get("/{thesis_id}", response_model=ThesisResponse)
async def get_thesis(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> ThesisResponse:
    svc = ThesisService(db)
    try:
        thesis = await svc.get(thesis_id=thesis_id, user_id=user_id)
    except ThesisNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Thesis #{thesis_id} not found.")
    return ThesisResponse.model_validate(thesis)


@router.delete("/{thesis_id}/close", status_code=status.HTTP_204_NO_CONTENT)
async def close_thesis(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    svc = ThesisService(db)
    try:
        await svc.close(thesis_id=thesis_id, user_id=user_id)
    except ThesisNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Thesis #{thesis_id} not found.")
    except ThesisAlreadyClosedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.delete("/{thesis_id}/invalidate", status_code=status.HTTP_204_NO_CONTENT)
async def invalidate_thesis(
    thesis_id: int,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    svc = ThesisService(db)
    try:
        await svc.invalidate(thesis_id=thesis_id, user_id=user_id)
    except ThesisNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Thesis #{thesis_id} not found.")
    except ThesisAlreadyClosedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
