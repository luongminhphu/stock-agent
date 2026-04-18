# briefing

Owner: narrative market brief generation.

**Boundary:** morning brief, end-of-day brief, watchlist-aware brief generation, formatting/templating for natural language briefing output.

**Contract:** Exposes `BriefingService` with `generate_morning_brief(user_id)` and `generate_eod_brief(user_id)`. Consumes market data and watchlist state; delegates AI narrative to `ai` segment agents.

**Do NOT put here:** raw quote fetching logic, thesis scoring, bot delivery mechanism.
