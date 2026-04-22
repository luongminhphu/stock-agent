"""Watchlist routes.

Owner: api segment.
Exposes watchlist CRUD via WatchlistService.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_db, get_scan_service
from src.api.dto.watchlist import (
    AddWatchlistItemRequest,
    WatchlistItemResponse,
    WatchlistListResponse,
)
from src.watchlist.service import (
    AddToWatchlistInput,
    WatchlistItemAlreadyExistsError,
    WatchlistItemNotFoundError,
    WatchlistService,
)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.post("", response_model=WatchlistItemResponse, status_code=status.HTTP_201_CREATED)
async def add_to_watchlist(
    body: AddWatchlistItemRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> WatchlistItemResponse:
    svc = WatchlistService(db)
    try:
        item = await svc.add(
            AddToWatchlistInput(
                user_id=user_id,
                ticker=body.ticker.upper(),
                note=body.note,
            )
        )
    except WatchlistItemAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{body.ticker.upper()}' is already in your watchlist.",
        ) from exc
    return WatchlistItemResponse.model_validate(item)


@router.get("", response_model=WatchlistListResponse)
async def list_watchlist(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> WatchlistListResponse:
    svc = WatchlistService(db)
    items = await svc.list_items(user_id)
    return WatchlistListResponse(items=[WatchlistItemResponse.model_validate(i) for i in items])


@router.delete("/{ticker}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_watchlist(
    ticker: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    svc = WatchlistService(db)
    try:
        await svc.remove(user_id=user_id, ticker=ticker.upper())
    except WatchlistItemNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{ticker.upper()}' not found in your watchlist.",
        ) from exc


@router.post("/scan", status_code=status.HTTP_200_OK)
async def trigger_scan(
    user_id: str = Depends(get_current_user_id),
    scan_svc=Depends(get_scan_service),
) -> dict:
    """Trigger watchlist scan thủ công, persist snapshot vào DB."""
    result = await scan_svc.scan_user(user_id=user_id)
    return {
        "status": "ok",
        "scanned_tickers": len(result.signals) + len(result.errors),
        "triggered": result.triggered_count,
        "summary": result.build_summary(),
    }
