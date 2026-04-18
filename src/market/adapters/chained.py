"""ChainedAdapter — try primary, fall back to secondary on any exception.

Owner: market segment.

Design:
- On fetch_quote: try primary first. If it raises ANY exception, log a warning
  and retry with secondary. If secondary also fails, re-raise the last error.
- On fetch_bulk_quotes: same strategy. Secondary is called for the entire
  batch if primary fails — no per-symbol granularity to keep it simple.
- Callers receive a Quote regardless of which adapter succeeded.
- Metrics: tracks source ('primary'/'secondary'/'failed') per call via logger.
"""

from __future__ import annotations

from src.market.quote_service import MarketDataAdapter, Quote
from src.platform.logging import get_logger

logger = get_logger(__name__)


class ChainedAdapter(MarketDataAdapter):
    """Adapter that chains a primary and a secondary provider.

    Args:
        primary:   First adapter to try (VCIAdapter).
        secondary: Fallback adapter (VNDirectAdapter).
    """

    def __init__(
        self,
        primary: MarketDataAdapter,
        secondary: MarketDataAdapter,
    ) -> None:
        self._primary = primary
        self._secondary = secondary

    async def fetch_quote(self, ticker: str) -> Quote:
        try:
            quote = await self._primary.fetch_quote(ticker)
            logger.debug("chained.quote.primary_ok", ticker=ticker)
            return quote
        except Exception as primary_exc:  # noqa: BLE001
            logger.warning(
                "chained.quote.primary_failed",
                ticker=ticker,
                error=str(primary_exc),
                fallback="secondary",
            )
            try:
                quote = await self._secondary.fetch_quote(ticker)
                logger.debug("chained.quote.secondary_ok", ticker=ticker)
                return quote
            except Exception as secondary_exc:  # noqa: BLE001
                logger.error(
                    "chained.quote.both_failed",
                    ticker=ticker,
                    primary_error=str(primary_exc),
                    secondary_error=str(secondary_exc),
                )
                raise secondary_exc

    async def fetch_bulk_quotes(self, tickers: list[str]) -> list[Quote]:
        try:
            quotes = await self._primary.fetch_bulk_quotes(tickers)
            logger.debug("chained.bulk.primary_ok", count=len(quotes))
            return quotes
        except Exception as primary_exc:  # noqa: BLE001
            logger.warning(
                "chained.bulk.primary_failed",
                tickers=tickers,
                error=str(primary_exc),
                fallback="secondary",
            )
            try:
                quotes = await self._secondary.fetch_bulk_quotes(tickers)
                logger.debug("chained.bulk.secondary_ok", count=len(quotes))
                return quotes
            except Exception as secondary_exc:  # noqa: BLE001
                logger.error(
                    "chained.bulk.both_failed",
                    tickers=tickers,
                    primary_error=str(primary_exc),
                    secondary_error=str(secondary_exc),
                )
                raise secondary_exc
