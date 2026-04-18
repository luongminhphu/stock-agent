# bot

Owner: Discord bot runtime and command routing only.

**Boundary:** Discord bot process, command handlers (thin cogs), scheduler orchestration (cron triggers only).

**Contract:** Each command handler resolves user intent and delegates to the appropriate domain service. No business logic lives here. Scheduler triggers briefing/scan services — it does NOT implement them.

**Do NOT put here:** thesis rules, watchlist logic, market data processing, AI prompting.
