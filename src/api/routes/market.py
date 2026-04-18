"""Market routes — expose readmodel market data.

Owner: api segment.
All data comes from readmodel or market segment services.
No business logic here.

Wave 1: /market/quote/{ticker} returns registry info only (no live price).
Wave 2: wire QuoteService adapter.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from src.api.dto.market import QuoteResponse, SymbolInfoResponse
from src.market.registry import SymbolNotFoundError, registry

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/symbols/{ticker}", response_model=SymbolInfoResponse)
async def get_symbol_info(ticker: str) -> SymbolInfoResponse:
    """Return registry information for a ticker."""
    try:
        info = registry.resolve(ticker.upper())
    except SymbolNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticker '{ticker.upper()}' not found in registry.",
        )
    return SymbolInfoResponse(
        ticker=info.ticker,
        name=info.name,
        exchange=info.exchange.value,
        sector=info.sector.value,
    )


@router.get("/quote/{ticker}", response_model=QuoteResponse)
async def get_quote(ticker: str) -> QuoteResponse:
    """Return live quote for a ticker.

    Wave 1: stub — validates ticker exists, returns placeholder.
    Wave 2: call QuoteService.get_quote(ticker) with real adapter.
    """
    try:
        info = registry.resolve(ticker.upper())
    except SymbolNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticker '{ticker.upper()}' not found in registry.",
        )
    # TODO Wave 2: inject QuoteService and return real price
    return QuoteResponse(
        ticker=info.ticker,
        name=info.name,
        price=None,
        change=None,
        change_pct=None,
        volume=None,
        note="Live quote not available yet (Wave 2).",
    )
