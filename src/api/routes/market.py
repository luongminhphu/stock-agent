"""Market routes — expose real-time quote and OHLCV history data.

Owner: api segment.
All data comes from market segment services via dependency injection.
No business logic here.

Endpoints:
    GET /market/symbols/{ticker}        — registry metadata only
    GET /market/quote/{ticker}          — live quote via QuoteService (VCI → VNDirect)
    GET /market/context/{ticker}        — quote + MA/RSI/ATR/vol/52w + trend + source_quality
    GET /market/ohlcv/{ticker}          — OHLCV candle history (default: last 30 trading days)
    GET /market/breadth                 — advance/decline/unchanged breadth snapshot
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import (
    get_breadth_service,
    get_ohlcv_service,
    get_quote_service,
    get_ticker_context_service,
)
from src.api.dto.market import (
    BreadthResponse,
    CandleResponse,
    QuoteResponse,
    SymbolInfoResponse,
    TickerContextResponse,
)
from src.market.breadth_service import BreadthService
from src.market.ohlcv_service import OHLCVService, OHLCVServiceNotConfiguredError
from src.market.quote_service import QuoteService, QuoteServiceNotConfiguredError
from src.market.registry import Exchange, SymbolNotFoundError, registry
from src.market.ticker_context import TickerContextService

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


@router.get("/context/{ticker}", response_model=TickerContextResponse)
async def get_ticker_context(
    ticker: str,
    tcs: TickerContextService = Depends(get_ticker_context_service),
) -> TickerContextResponse:
    """Wave U2a: TickerContext cho FE — cùng nguồn AI review / stop-breach dùng.

    Thin adapter: map dataclass → DTO, không tính toán lại chỉ báo.
    """
    ticker = ticker.upper()
    try:
        info = registry.resolve(ticker)
    except SymbolNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticker '{ticker}' not found in registry.",
        ) from exc

    try:
        ctx = await tcs.get(ticker)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Market context unavailable for '{ticker}': {exc}",
        ) from exc

    q = ctx.quote
    return TickerContextResponse(
        ticker=ctx.ticker,
        name=info.name,
        as_of=ctx.as_of,
        price=q.price,
        change=q.change,
        change_pct=q.change_pct,
        volume=q.volume,
        open=q.open,
        high=q.high,
        low=q.low,
        ref_price=q.ref_price,
        ceiling=q.ceiling,
        floor=q.floor,
        is_ceiling=q.is_ceiling,
        is_floor=q.is_floor,
        formatted_price=q.format_price(),
        ma20=ctx.ma20,
        ma50=ctx.ma50,
        rsi14=round(ctx.rsi14, 1) if ctx.rsi14 is not None else None,
        atr14=ctx.atr14,
        vol_ratio_20=round(ctx.vol_ratio_20, 2) if ctx.vol_ratio_20 is not None else None,
        hi_52w=ctx.hi_52w,
        lo_52w=ctx.lo_52w,
        dist_to_ma20_pct=round(ctx.dist_to_ma20_pct, 2)
        if ctx.dist_to_ma20_pct is not None
        else None,
        dist_to_hi_52w_pct=round(ctx.dist_to_hi_52w_pct, 2)
        if ctx.dist_to_hi_52w_pct is not None
        else None,
        bars=ctx.bars,
        trend_state=str(ctx.trend_state),
        source_quality=str(ctx.source_quality),
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


@router.get("/breadth", response_model=BreadthResponse)
async def get_market_breadth(
    exchange: str | None = Query(
        default=None,
        description="Exchange scope: HOSE | HNX | UPCOM | ALL (default: ALL)",
    ),
    breadth_svc: BreadthService = Depends(get_breadth_service),
) -> BreadthResponse:
    """Return advance/decline/unchanged breadth snapshot for the given exchange.

    Uses the symbol registry as the universe — covers VN30 + top liquidity tickers.
    Label on UI should read \"in registry\" not \"full market\".

    Raises 400 for invalid exchange string.
    Raises 503 if QuoteService adapter is not configured.
    Raises 502 on upstream market data error.
    """
    ex: Exchange | None = None
    if exchange:
        try:
            ex = Exchange(exchange.upper())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid exchange '{exchange}'. Valid values: HOSE, HNX, UPCOM.",
            ) from exc

    try:
        result = await breadth_svc.get_breadth(ex)
    except QuoteServiceNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Market data adapter not configured.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Market breadth unavailable: {exc}",
        ) from exc

    return BreadthResponse(
        exchange=result.exchange,
        advance=result.advance,
        decline=result.decline,
        unchanged=result.unchanged,
        ceiling=result.ceiling,
        floor=result.floor,
        total=result.total,
        advance_pct=result.advance_pct,
        decline_pct=result.decline_pct,
        unchanged_pct=result.unchanged_pct,
    )
