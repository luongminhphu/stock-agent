"""Market routes — expose real-time quote and OHLCV history data.

Owner: api segment.
All data comes from market segment services via dependency injection.
No business logic here.

Endpoints:
    GET /market/symbols/{ticker}        — registry metadata only
    GET /market/quote/{ticker}          — live quote via QuoteService (VCI → VNDirect)
    GET /market/ohlcv/{ticker}          — OHLCV candle history (default: last 30 trading days)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import get_ohlcv_service, get_quote_service
from src.api.dto.market import CandleResponse, QuoteResponse, SymbolInfoResponse
from src.market.ohlcv_service import OHLCVService, OHLCVServiceNotConfiguredError
from src.market.quote_service import QuoteService
from src.market.registry import SymbolNotFoundError, registry

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/symbols/{ticker}", response_model=SymbolInfoResponse)
async def get_symbol_info(ticker: str) -> SymbolInfoResponse:
    """Return registry metadata for a ticker."""
    try:
        info = registry.resolve(ticker.upper())
    except SymbolNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticker '{ticker.upper()}' not found in registry.",
        ) from exc
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
    except SymbolNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticker '{ticker}' not found in registry.",
        ) from exc

    try:
        quote = await quote_svc.get_quote(ticker)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Market data unavailable for '{ticker}': {exc}",
        ) from exc

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


@router.get("/ohlcv/{ticker}", response_model=list[CandleResponse])
async def get_ohlcv(
    ticker: str,
    days: int = Query(default=30, ge=5, le=180, description="Number of trading days to return"),
    ohlcv_svc: OHLCVService = Depends(get_ohlcv_service),
) -> list[CandleResponse]:
    """Return OHLCV candle history for a ticker.

    Used by the thesis detail price mini chart.
    Returns up to `days` most-recent 1D candles, oldest-first.
    """
    ticker = ticker.upper()

    # Validate ticker in registry
    try:
        registry.resolve(ticker)
    except SymbolNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticker '{ticker}' not found in registry.",
        ) from exc

    try:
        candles = await ohlcv_svc.get_latest_candles(ticker, n=days)
    except OHLCVServiceNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OHLCV service not configured.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OHLCV data unavailable for '{ticker}': {exc}",
        ) from exc

    return [
        CandleResponse(
            date=c.date,
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
        )
        for c in candles
    ]
