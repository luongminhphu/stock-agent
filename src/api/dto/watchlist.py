"""Watchlist DTOs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AddWatchlistItemRequest(BaseModel):
    ticker: str
    note: str = ""


class UpdateNoteRequest(BaseModel):
    """Body for PATCH /watchlist/{ticker}/note."""

    note: str = Field(..., description="New note text. Pass empty string to clear.")


class WatchlistItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    note: str | None
    thesis_id: int | None
    added_at: datetime


class WatchlistListResponse(BaseModel):
    items: list[WatchlistItemResponse]
    total: int = 0

    def model_post_init(self, __context: object) -> None:
        self.total = len(self.items)
