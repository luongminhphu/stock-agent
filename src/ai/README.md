# ai

Owner: AI orchestration layer.

**Boundary:** Perplexity client (with retry/circuit breaker), prompt packs, structured output schemas, general investor agent, thesis review agent.

**Contract:** Exposes typed async callables that return Pydantic structured outputs (e.g. `ThesisReviewOutput`). Domain segments call AI agents; AI agents do NOT own domain rules.

**Do NOT put here:** thesis business rules, watchlist logic, market data fetching.
