"""MemoryService — read/write interface for the ai.memory system.

Write path: agents call log_interaction() after every AI call.
Read path:  ContextBuilder calls get_memory_context() to build the
            memory block injected into every prompt.

Owner: ai segment.
Callers:
  - ai/agents/*.py         → log_interaction
  - ai/context_builder.py  → get_memory_context
  - ai/memory/consolidator.py (internal)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.memory.models import AIInteractionLog, MemorySnapshot
from src.ai.memory.repository import InteractionLogRepository, MemorySnapshotRepository
from src.platform.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


@dataclass
class InteractionEntry:
    """Value object passed by agents to log_interaction().

    Agents extract fields from their AI output schema and populate this.
    Keeps agents decoupled from ORM model internals.
    """

    user_id: str
    agent_type: str
    trigger: str = "unknown"
    tickers: list[str] = field(default_factory=list)
    ai_verdict: str | None = None
    ai_confidence: float | None = None
    ai_key_points: str | None = None        # newline-separated prose
    ai_risk_signals: str | None = None      # newline-separated prose
    thesis_id: int | None = None
    decision_id: int | None = None


@dataclass
class MemoryContext:
    """Assembled memory context passed to ContextBuilder.

    Contains both recent episodic excerpts (L2) and the latest
    semantic snapshot (L3). ContextBuilder renders this into a
    prompt block via .render() or as_context_block().
    """

    user_id: str
    recent_episodes: list[AIInteractionLog] = field(default_factory=list)
    latest_snapshot: MemorySnapshot | None = None

    def is_empty(self) -> bool:
        return not self.recent_episodes and self.latest_snapshot is None

    def render(self) -> str:
        """Render full memory context for injection into AI prompts.

        Structure:
          [Semantic memory — from latest MemorySnapshot]
          [Recent interactions — last N episodic entries]
        """
        parts: list[str] = []

        # Layer 3: Semantic snapshot
        if self.latest_snapshot:
            block = self.latest_snapshot.as_context_block()
            if block:
                parts.append(block)

        # Layer 2: Recent episodes (compact format)
        if self.recent_episodes:
            episode_lines = ["[Recent AI interactions — newest first]"]
            for ep in self.recent_episodes[:10]:  # cap at 10 for prompt budget
                line_parts = [
                    ep.created_at.strftime("%Y-%m-%d %H:%M"),
                    ep.agent_type,
                ]
                if ep.tickers:
                    line_parts.append(",".join(ep.tickers))
                if ep.ai_verdict:
                    line_parts.append(f"verdict={ep.ai_verdict}")
                if ep.ai_confidence is not None:
                    line_parts.append(f"conf={ep.ai_confidence:.0%}")
                episode_lines.append(" | ".join(line_parts))
                # Key points on next line, indented
                if ep.ai_key_points:
                    for kp in ep.ai_key_points.splitlines()[:2]:  # max 2 lines
                        episode_lines.append(f"  → {kp.strip()}")
            parts.append("\n".join(episode_lines))

        return "\n\n".join(parts)


class MemoryService:
    """Stateless service — accepts session per call."""

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    @staticmethod
    async def log_interaction(
        session: AsyncSession,  # kept for backward compat — no longer used for write
        entry: InteractionEntry,
    ) -> AIInteractionLog | None:
        """Persist one episodic memory entry in an ISOLATED session.

        Always opens its own AsyncSessionLocal session so that a log
        failure (e.g. table missing, constraint violation) can never
        poison the caller's transaction.

        The `session` param is retained for backward compatibility with
        all existing agents — it is intentionally unused here. Agents
        do not need to change their call sites.

        Fire-and-forget: all exceptions are swallowed, returns None on
        failure so callers can safely ignore the return value.
        """
        try:
            # Lazy import avoids circular dependency at module load time.
            from src.platform.db import AsyncSessionLocal  # noqa: PLC0415

            async with AsyncSessionLocal() as log_session:
                async with log_session.begin():
                    return await MemoryService._do_log(log_session, entry)
        except Exception as exc:
            logger.warning(
                "memory_service.log_interaction.failed",
                user_id=entry.user_id,
                agent_type=entry.agent_type,
                error=str(exc),
            )
            return None

    @staticmethod
    async def _do_log(
        session: AsyncSession,
        entry: InteractionEntry,
    ) -> AIInteractionLog | None:
        """Internal: write one log row. Caller owns session/transaction."""
        log = AIInteractionLog(
            user_id=entry.user_id,
            agent_type=entry.agent_type,
            trigger=entry.trigger,
            ai_verdict=entry.ai_verdict,
            ai_confidence=entry.ai_confidence,
            ai_key_points=entry.ai_key_points,
            ai_risk_signals=entry.ai_risk_signals,
            thesis_id=entry.thesis_id,
            decision_id=entry.decision_id,
        )
        log.tickers = entry.tickers
        repo = InteractionLogRepository(session)
        saved = await repo.save(log)
        logger.debug(
            "memory_service.log_interaction.saved",
            user_id=entry.user_id,
            agent_type=entry.agent_type,
            verdict=entry.ai_verdict,
        )
        return saved

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    @staticmethod
    async def get_memory_context(
        session: AsyncSession,
        user_id: str,
        episode_limit: int = 15,
    ) -> MemoryContext:
        """Assemble full memory context for a user.

        Returns an empty MemoryContext (not None) when no data exists,
        so callers never need to null-check.
        """
        try:
            episode_repo = InteractionLogRepository(session)
            snapshot_repo = MemorySnapshotRepository(session)

            episodes, snapshot = await _gather(
                episode_repo.get_recent(user_id, limit=episode_limit),
                snapshot_repo.get_latest(user_id),
            )
            return MemoryContext(
                user_id=user_id,
                recent_episodes=episodes,
                latest_snapshot=snapshot,
            )
        except Exception as exc:
            logger.warning(
                "memory_service.get_memory_context.failed",
                user_id=user_id,
                error=str(exc),
            )
            return MemoryContext(user_id=user_id)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402


async def _gather(coro_a, coro_b):
    """Run two coroutines concurrently and return both results."""
    return await asyncio.gather(coro_a, coro_b)
