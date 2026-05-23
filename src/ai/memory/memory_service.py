"""MemoryService — read/write interface for the ai.memory system.

Write path: agents call log_interaction() after every AI call.
            post-mortem pipeline calls append() to inject free-text lessons.
Read path:  ContextBuilder calls get_memory_context() to build the
            memory block injected into every prompt.

Owner: ai segment.
Callers:
  - ai/agents/*.py              → log_interaction
  - ai/memory_injection_listener.py → append
  - ai/context_builder.py       → get_memory_context
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

# Max episodes rendered into the prompt context block.
# Also used as the default fetch limit in get_memory_context().
# Change here propagates to both fetch and render — no silent drift.
_PROMPT_EPISODE_CAP = 10


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
            for ep in self.recent_episodes[:_PROMPT_EPISODE_CAP]:
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
        failure can never poison the caller's transaction.

        The `session` param is retained for backward compatibility with
        all existing agents — it is intentionally unused here and will
        be removed in a future wave once all call sites are updated.

        Fire-and-forget: all exceptions are swallowed, returns None on
        failure so callers can safely ignore the return value.
        """
        try:
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

    @staticmethod
    async def append(
        session: AsyncSession,
        user_id: str,
        content: str,
        tags: list[str] | None = None,
        source: str = "post_mortem",
    ) -> AIInteractionLog | None:
        """Persist a free-text memory entry in an ISOLATED session.

        Used by MemoryInjectionListener to write post-mortem lessons.
        Stores content as ai_key_points, source as agent_type, tags as tickers
        so entries surface naturally in get_memory_context() episode renders.

        The `session` param is accepted for API consistency but not used —
        same isolation pattern as log_interaction().

        Fire-and-forget: all exceptions are swallowed, returns None on failure.
        """
        try:
            from src.platform.db import AsyncSessionLocal  # noqa: PLC0415

            async with AsyncSessionLocal() as write_session:
                async with write_session.begin():
                    log = AIInteractionLog(
                        user_id=user_id,
                        agent_type=source,
                        trigger=source,
                        ai_key_points=content,
                    )
                    log.tickers = tags or []
                    repo = InteractionLogRepository(write_session)
                    saved = await repo.save(log)
                    logger.debug(
                        "memory_service.append.saved",
                        user_id=user_id,
                        source=source,
                        tags=tags,
                    )
                    return saved
        except Exception as exc:
            logger.warning(
                "memory_service.append.failed",
                user_id=user_id,
                source=source,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    @staticmethod
    async def get_memory_context(
        session: AsyncSession,
        user_id: str,
        episode_limit: int = _PROMPT_EPISODE_CAP,
        thesis_id: int | None = None,
    ) -> MemoryContext:
        """Assemble full memory context for a user.

        Args:
            session:       Active AsyncSession.
            user_id:       Owner of the memory.
            episode_limit: Max raw episodes fetched before filtering.
                           Defaults to _PROMPT_EPISODE_CAP so fetch and
                           render caps stay in sync automatically.
            thesis_id:     Optional — when provided, filters episodes to
                           those logged for this thesis (ep.thesis_id matches)
                           or legacy rows with no thesis_id (ep.thesis_id is
                           None). This prevents cross-thesis memory bleed.
                           Callers that omit thesis_id get the full user-level
                           memory (backward-compatible).

        Each query (episodes, snapshot) fails independently — a failure
        in one does not abort the other. Returns an empty MemoryContext
        (not None) when no data exists, so callers never need to null-check.

        Note: queries are run sequentially (not via asyncio.gather) because
        SQLAlchemy 2.0 AsyncSession does not support concurrent operations
        on the same session object (raises ISCE on gather).
        """
        episodes: list[AIInteractionLog] = []
        snapshot: MemorySnapshot | None = None

        episode_repo = InteractionLogRepository(session)
        snapshot_repo = MemorySnapshotRepository(session)

        try:
            episodes = await episode_repo.get_recent(user_id, limit=episode_limit)
        except Exception as exc:
            logger.warning(
                "memory_service.episodes_failed",
                user_id=user_id,
                error=str(exc),
            )

        try:
            snapshot = await snapshot_repo.get_latest(user_id)
        except Exception as exc:
            logger.warning(
                "memory_service.snapshot_failed",
                user_id=user_id,
                error=str(exc),
            )

        # Scope episodes to thesis when caller provides thesis_id.
        # Legacy rows (ep.thesis_id is None) are kept to avoid losing
        # historical context that was logged before thesis_id tracking.
        if thesis_id is not None and episodes:
            episodes = [
                ep for ep in episodes
                if ep.thesis_id is None or ep.thesis_id == thesis_id
            ]
            logger.debug(
                "memory_service.episodes_filtered_by_thesis",
                user_id=user_id,
                thesis_id=thesis_id,
                count=len(episodes),
            )

        return MemoryContext(
            user_id=user_id,
            recent_episodes=episodes,
            latest_snapshot=snapshot,
        )
