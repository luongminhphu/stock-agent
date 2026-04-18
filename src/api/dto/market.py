"""Market DTOs."""
from __future__ import annotations
from pydantic import BaseModel


class SymbolInfoResponse(BaseModel):
    ticker: str
    name: str
    exchange: str
    sector: str


class QuoteResponse(BaseModel):
    ticker: str
    name: str
    price: float | None
    change: float | None
    change_pct: float | None
    volume: int | None
    note: str | None = None
