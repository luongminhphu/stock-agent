"""PriceEnrichmentService — injects live prices into readmodel DTOs.

Owner: market segment.

This is the ONLY place where market segment touches readmodel DTOs.
readmodel services produce DTOs with current_price=None / pnl_pct=None;
this service fills those fields using QuoteService.

Design rules:
- Single bulk fetch per call (no N+1).
- Failures on individual tickers are swallowed and logged; the rest of
  the dashboard must still render.
- Never import thesis/watchlist/briefing domain models.
"""

from __future__ import annotations

import asyncio
from typing import TypeVar

from src.market.quote_service import QuoteService
from src.platform.logging import get_logger

logger = get_logger(__name__)


class PriceEnrichmentService:
    def __init__(self, quote_service: QuoteService) -> None:
        self._qs = quote_service

    # ------------------------------------------------------------------
    # Public helpers — called by API routes / bot after readmodel query
    # ------------------------------------------------------------------

    async def enrich_dashboard(self, response: object) -> object:
        """Mutate ThesisSummaryRow.current_price and .pnl_pct in-place.

        Accepts a DashboardResponse; returns it enriched.
        Works on the schema type without importing it (duck-typed).
        """
        rows = getattr(response, "theses", [])
        if not rows:
            return response

        tickers = list({r.ticker for r in rows})
        price_map = await self._bulk_fetch(tickers)

        for row in rows:
            price = price_map.get(row.ticker)
            if price is not None:
                # Use object.__setattr__ because Pydantic v2 models may be frozen
                try:
                    row.current_price = price
                    if row.entry_price and row.entry_price > 0:
                        row.pnl_pct = (price - row.entry_price) / row.entry_price * 100
                except Exception:
                    # If model is truly immutable, rebuild — but schemas use ConfigDict(from_attributes=True)
                    # and are NOT frozen, so direct assignment works.
                    pass
        return response

    async def enrich_watchlist(self, rows: list) -> list:
        """Mutate WatchlistSnapshotRow.current_price in-place."""
        if not rows:
            return rows
        tickers = list({r.ticker for r in rows})
        price_map = await self._bulk_fetch(tickers)
        for row in rows:
            row.current_price = price_map.get(row.ticker)
        return rows

    async def get_prices(self, tickers: list[str]) -> dict[str, float]:
        """Raw bulk price fetch — returns {ticker: price}."""
        return await self._bulk_fetch(tickers)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _bulk_fetch(self, tickers: list[str]) -> dict[str, float]:
        """Fetch all tickers in one adapter call; return {TICKER: price}."""
        if not tickers:
            return {}
        try:
            quotes = await self._qs.get_bulk_quotes(tickers)
            return {q.ticker: q.price for q in quotes}
        except Exception as exc:
            logger.warning(
                "market.price_enrichment.bulk_fetch_failed",
                tickers=tickers,
                error=str(exc),
            )
            # Fall back to individual fetches so partial data beats no data
            results: dict[str, float] = {}
            tasks = {t: asyncio.create_task(self._safe_single(t)) for t in tickers}
            for ticker, task in tasks.items():
                price = await task
                if price is not None:
                    results[ticker] = price
            return results

    async def _safe_single(self, ticker: str) -> float | None:
        try:
            quote = await self._qs.get_quote(ticker)
            return quote.price
        except Exception as exc:
            logger.warning(
                "market.price_enrichment.single_fetch_failed",
                ticker=ticker,
                error=str(exc),
            )
            return None
