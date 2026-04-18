"""Thesis DTOs."""
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class CreateThesisRequest(BaseModel):
    ticker: str
    title: str
    summary: str = ""
    entry_price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None


class ThesisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    title: str
    status: str
    entry_price: float | None
    target_price: float | None
    stop_loss: float | None
    upside_pct: float | None
    risk_reward: float | None
    score: float | None
    created_at: datetime


class ThesisListResponse(BaseModel):
    items: list[ThesisResponse]
    total: int = 0

    def model_post_init(self, __context: object) -> None:
        self.total = len(self.items)
