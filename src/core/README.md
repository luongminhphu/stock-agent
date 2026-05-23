# Core Intelligence Engine

**Owner:** `core` segment  
**Role:** Cross-segment orchestration brain. Observer, ranker, and verdict emitter.

---

## Responsibilities

- Aggregate cross-segment state into a `SystemSnapshot`
- Rank signals by urgency across all segments
- Derive a structured `EngineVerdict` (verdict, confidence, action, risk signals)
- Emit `IntelligenceEngineCompletedEvent` to `briefing` and `bot`
- Does **not** contain Discord logic, DB models, or domain rules from other segments

## Loop Position

```
[Scheduler / User / Event]
        ↓ IntelligenceEngineRequestedEvent
IntelligenceEngineListener._handle()
        ↓
  snapshot.build_snapshot()     ← asyncio.gather (watchlist, thesis, portfolio)
        ↓
  signals.rank_signals()        ← deterministic scoring, no AI cost
        ↓
  engine._derive_verdict()      ← heuristic Wave 1 / AI Wave 2
        ↓ (if confidence ≥ 0.55)
IntelligenceEngineCompletedEvent
        ↓              ↓
briefing (inject)    bot.EngineSubscriber (Discord embed)
```

## Module Map

| File | Responsibility |
|------|----------------|
| `schemas.py` | Pydantic/dataclass contracts: snapshot, signals, verdict |
| `snapshot.py` | Parallel cross-segment state fetch |
| `signals.py` | Deterministic signal ranker |
| `engine.py` | Orchestration cycle: snapshot → rank → verdict |
| `intelligence_listener.py` | EventBus wiring |

## Ownership Rules

- `core` reads from other segments, **never writes** to their tables
- `bot` and `api` trigger engine via `IntelligenceEngineRequestedEvent`, never call `engine.run_cycle()` directly
- Dispatch to `briefing` / `bot` is always via events, never direct function calls

## Wave Roadmap

| Wave | Scope |
|------|-------|
| **Wave 1** (current) | Deterministic heuristic verdict, zero AI cost |
| **Wave 2** | AI synthesis in `engine._derive_verdict()` via `ai` segment |
| **Wave 3** | `feedback.py` — outcome recording, signal weight adjustment |
| **Wave 4** | `evolution.py` — self-improvement suggestions via `EvolutionLog` |

## Guardrails

- Each `_fetch_*` in `snapshot.py` uses an isolated DB session — one failure does not cascade
- `priority="high"` bypasses the confidence threshold (for urgent/manual triggers)
- Wave 4 self-improvement **never auto-applies** code changes — all suggestions go to `EvolutionLog` for human review
