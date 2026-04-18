# watchlist

Owner: watchlist state and alert management.

**Boundary:** watchlist CRUD, scan service (price/signal triggers), alert service, reminder service, related repositories and models.

**Contract:** Exposes `WatchlistService`, `ScanService`, `AlertService`. May reference `Thesis.id` from thesis segment but imports no thesis business logic.

**Do NOT put here:** thesis review rules, market data adapters, bot command parsing.
