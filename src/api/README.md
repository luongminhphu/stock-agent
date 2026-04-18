# api

Owner: HTTP API surface.

**Boundary:** FastAPI app, route definitions, DTOs/response models, OpenAPI schema.

**Contract:** Routes expose capabilities from `readmodel` and domain services only. No business logic in route handlers. Thin as possible — validate input, call service, return DTO.

**Do NOT put here:** domain logic, AI prompts, direct DB queries outside readmodel.
