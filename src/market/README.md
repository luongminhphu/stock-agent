# market

Owner: market data.

**Boundary:** symbol registry (HOSE/HNX/UPCoM), quote service, OHLCV history, technical/context/news service, market data adapters (VNDIRECT, SSI, FireAnt, etc.).

**Contract:** Exposes `QuoteService`, `OHLCVService`, and `SymbolRegistry` as the public API. Adapters are internal implementation details.

**Do NOT put here:** thesis logic, watchlist state, AI prompts.
