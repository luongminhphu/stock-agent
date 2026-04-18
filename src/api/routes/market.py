"""Market routes — expose real-time quote data.

Owner: api segment.
All data comes from market segment services via dependency injection.
No business logic here.

Endpoints:
    GET /market/symbols/{ticker}  — registry metadata only
    GET /market/quote/{ticker}    — live quote via QuoteService (VCI → VNDirect)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_quote_service
from src.api.dto.market import QuoteResponse, SymbolInfoResponse
from src.market.quote_service import QuoteService
from src.market.registry import SymbolNotFoundError, registry

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/symbols/{ticker}", response_model=SymbolInfoResponse)
async def get_symbol_info(ticker: str) -> SymbolInfoResponse:
    """Return registry metadata for a ticker."""
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
async def get_quote(
    ticker: str,
    quote_svc: QuoteService = Depends(get_quote_service),
) -> QuoteResponse:
    """Return live quote for a ticker via ChainedAdapter (VCI → VNDirect)."""
    ticker = ticker.upper()

    # Validate ticker exists in registry before hitting external API
    try:
        info = registry.resolve(ticker)
    except SymbolNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticker '{ticker}' not found in registry.",
        )

    try:
        quote = await quote_svc.get_quote(ticker)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Market data unavailable for '{ticker}': {exc}",
        )

    return QuoteResponse(
        ticker=quote.ticker,
        name=info.name,
        price=quote.price,
        change=quote.change,
        change_pct=quote.change_pct,
        volume=quote.volume,
        value=quote.value,
        open=quote.open,
        high=quote.high,
        low=quote.low,
        ref_price=quote.ref_price,
        ceiling=quote.ceiling,
        floor=quote.floor,
        is_ceiling=quote.is_ceiling,
        is_floor=quote.is_floor,
        formatted_price=quote.format_price(),
        formatted_change=quote.format_change(),
        timestamp=quote.timestamp,
    )
