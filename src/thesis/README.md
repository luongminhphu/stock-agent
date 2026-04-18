# thesis

Owner: thesis lifecycle and all thesis-related business rules.

**Boundary:** thesis CRUD lifecycle, assumptions, catalysts, review service, scoring service, invalidation service, snapshot/performance tracking, thesis repositories and models.

**Contract:** Exposes `ThesisService`, `ReviewService`, `ScoringService`, `InvalidationService`. Watchlist and briefing segments may reference thesis IDs but must NOT import thesis business rules directly.

**Do NOT put here:** market data fetching, AI client calls, bot/scheduler logic.
