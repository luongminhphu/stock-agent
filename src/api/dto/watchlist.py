"""Watchlist DTOs."""

from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class AddWatchlistItemRequest(BaseModel):
    ticker: str
    note: str = ""


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
