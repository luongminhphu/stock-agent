"""ai.memory — public surface for the 3-layer memory system.

Layer 1 — Working Memory  : built per-call by ContextBuilder (already exists)
Layer 2 — Episodic Memory : AIInteractionLog — every AI call is logged here
Layer 3 — Semantic Memory : MemorySnapshot  — weekly distillation of episodes

Public API (what callers outside this sub-module should use):
  log_interaction(session, entry) -> None
  get_memory_context(session, user_id, limit) -> MemoryContext

Owner: ai segment.
Callers allowed: ai/agents/*.py, ai/context_builder.py, bot/scheduler.py (consolidator only).
Not allowed: bot commands, briefing service, thesis service — they must go through agents.
"""

from __future__ import annotations

from src.ai.memory.memory_service import MemoryService
from src.ai.memory.models import AIInteractionLog, MemorySnapshot

__all__ = [
    "AIInteractionLog",
    "MemorySnapshot",
    "MemoryService",
]
