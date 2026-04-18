# readmodel

Owner: read-optimized projections for UI and queries.

**Boundary:** dashboard service, leaderboard service, thesis timeline read queries, optimized read queries and projections for API/bot consumption.

**Contract:** All queries here are read-only. No writes. No business logic. Exposes `DashboardService`, `LeaderboardService`, `ThesisTimelineQuery`. Write-side mutations must go through domain segment services.

**Do NOT put here:** write-side business logic, AI calls, market data fetching.
