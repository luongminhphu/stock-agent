# platform

Owner: infra-level concerns only.

**Boundary:** config, settings, DB connection/session, structured logging, health bootstrap, DI container/runtime.

**Contract:** All other segments import `Settings`, `AsyncSessionLocal`, `Base`, and `configure_logging` from here. No segment leaks infra concerns outside this boundary.

**Do NOT put here:** business rules, domain models, AI prompts, market data adapters.
