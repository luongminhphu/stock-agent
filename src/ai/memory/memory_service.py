"""MemoryService — read/write interface for the ai.memory system.

Write path: agents call log_interaction() after every AI call.
            post-mortem pipeline calls append() to inject free-text lessons.
            bot.SignalReactionListener calls log_user_signal() on emoji react.
Read path:  ContextBuilder calls get_memory_context() to build the
            memory block injected into every prompt.

Owner: ai segment.
Callers:
  - ai/agents/*.py                     → log_interaction
  - ai/memory_injection_listener.py    → append
  - ai/context_builder.py              → get_memory_context
  - bot/signal_reaction_listener.py    → log_user_signal
  - ai/memory/consolidator.py (internal)
"""

from __future__ import annotations

import asyncio
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
_PROMPT_EPISODE_CAP = 10

# Auto-consolidation: synthesize after every N new interactions.
# Keeps memory fresh without waiting for the weekly Sunday scheduler.
_AUTO_CONSOLIDATE_EVERY = 10

# Valid user_signal values — mirrors UserBehaviorLog.signal
VALID_USER_SIGNALS = frozenset({"bought", "sold", "watched", "ignored", "flagged"})

# W5B: agent_types written by W5A that represent invalidation events.
# These are rendered in a dedicated [Invalidation History] section so the
# agent sees WHY assumptions/catalysts disappeared, not just that they did.
_INVALIDATION_AGENT_TYPES = frozenset({"assumption_invalidated", "catalyst_cancelled"})


@dataclass
class InteractionEntry:
    """Value object passed by agents to log_interaction()."""

    user_id: str
    agent_type: str
    trigger: str = "unknown"
    tickers: list[str] = field(default_factory=list)
    ai_verdict: str | None = None
    ai_confidence: float | None = None
    ai_key_points: str | None = None
    ai_risk_signals: str | None = None
    thesis_id: int | None = None
    decision_id: int | None = None


@dataclass
class MemoryContext:
    """Assembled memory context passed to ContextBuilder."""

    user_id: str
    recent_episodes: list[AIInteractionLog] = field(default_factory=list)
    latest_snapshot: MemorySnapshot | None = None

    def is_empty(self) -> bool:
        return not self.recent_episodes and self.latest_snapshot is None

    def render(self) -> str:
        """Render full memory context for injection into AI prompts.

        W5B: Invalidation episodes (assumption_invalidated, catalyst_cancelled)
        are extracted from recent_episodes and rendered in a dedicated
        [Invalidation History] section BEFORE the general activity section.
        This ensures the agent sees WHY assumptions/catalysts were removed
        even when the invalidation event has been pushed down by newer episodes.

        Section order (each optional — omitted when empty):
          1. Semantic snapshot (latest_snapshot)
          2. [Invalidation History] — assumption/catalyst invalidation events
          3. [Recent AI interactions] — all other episodic events
        """
        parts: list[str] = []

        if self.latest_snapshot:
            block = self.latest_snapshot.as_context_block()
            if block:
                parts.append(block)

        # W5B: split episodes into invalidation vs general
        invalidation_eps = [
            ep for ep in self.recent_episodes
            if ep.agent_type in _INVALIDATION_AGENT_TYPES
        ]
        general_eps = [
            ep for ep in self.recent_episodes
            if ep.agent_type not in _INVALIDATION_AGENT_TYPES
        ]

        # W5B: render invalidation history section
        if invalidation_eps:
            inv_lines = ["[Invalidation History — assumptions/catalysts removed by AI]"]
            for ep in invalidation_eps:
                ticker_str = ",".join(ep.tickers) if ep.tickers else "?"
                label = "ASSUMPTION" if ep.agent_type == "assumption_invalidated" else "CATALYST"
                date_str = ep.created_at.strftime("%Y-%m-%d")
                header = f"{date_str} | {label} INVALIDATED | {ticker_str}"
                inv_lines.append(header)
                if ep.ai_key_points:
                    inv_lines.append(f"  what: {ep.ai_key_points.strip()}")
                if ep.ai_risk_signals:
                    inv_lines.append(f"  why:  {ep.ai_risk_signals.strip()}")
            parts.append("\n".join(inv_lines))

        # General episodic activity (excludes invalidation events)
        if general_eps:
            episode_lines = ["[Recent AI interactions — newest first]"]
            for ep in general_eps[:_PROMPT_EPISODE_CAP]:
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
                if ep.ai_key_points:
                    for kp in ep.ai_key_points.splitlines()[:2]:
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
        session: AsyncSession,
        entry: InteractionEntry,
    ) -> AIInteractionLog | None:
        """Persist one episodic memory entry in an ISOLATED session.

        The `session` param is retained for backward compatibility.
        Fire-and-forget: all exceptions are swallowed.

        Auto-consolidation: every _AUTO_CONSOLIDATE_EVERY interactions, trigger
        MemoryConsolidator.run() as a background task so memory snapshot stays
        fresh without waiting for the weekly Sunday scheduler.
        """
        try:
            from src.platform.db import AsyncSessionLocal  # noqa: PLC0415

            async with AsyncSessionLocal() as log_session:
                async with log_session.begin():
                    saved = await MemoryService._do_log(log_session, entry)

            # Auto-consolidation check — fire-and-forget, never raises
            asyncio.ensure_future(
                MemoryService._maybe_consolidate(entry.user_id)
            )
            return saved
        except Exception as exc:
            logger.warning(
                "memory_service.log_interaction.failed",
                user_id=entry.user_id,
                agent_type=entry.agent_type,
                error=str(exc),
            )
            return None

    @staticmethod
    async def _maybe_consolidate(user_id: str) -> None:
        """Trigger MemoryConsolidator.run() every N interactions.

        Checks total interaction count for user mod _AUTO_CONSOLIDATE_EVERY.
        Fire-and-forget: all exceptions are swallowed.
        """
        try:
            from src.platform.db import AsyncSessionLocal  # noqa: PLC0415
            from src.platform.bootstrap import get_ai_client  # noqa: PLC0415
            from src.ai.memory.consolidator import MemoryConsolidator  # noqa: PLC0415

            async with AsyncSessionLocal() as session:
                repo = InteractionLogRepository(session)
                count = await repo.count_by_user(user_id)

            if count % _AUTO_CONSOLIDATE_EVERY != 0:
                return  # not yet at threshold

            logger.info(
                "memory_service.auto_consolidate.triggered",
                user_id=user_id,
                interaction_count=count,
            )
            ai_client = get_ai_client()
            if ai_client is None:
                return

            consolidator = MemoryConsolidator(client=ai_client, user_id=user_id)
            async with AsyncSessionLocal() as session:
                await consolidator.run(session)

            logger.info(
                "memory_service.auto_consolidate.done",
                user_id=user_id,
                interaction_count=count,
            )
        except Exception as exc:
            logger.warning(
                "memory_service.auto_consolidate.failed",
                user_id=user_id,
                error=str(exc),
            )

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

    @staticmethod
    async def log_user_signal(
        user_id: str,
        signal: str,
        ticker: str | None = None,
        interaction_log_id: int | None = None,
        agent_type: str | None = None,
        source: str = "discord_reaction",
        note: str | None = None,
    ) -> bool:
        """Record a deliberate investor action as a UserBehaviorLog row.

        Wave B: writes to user_behavior_logs (clean investor signal table)
        AND back-fills AIInteractionLog.user_signal on the linked row so
        pattern synthesis queries continue to work without schema change.

        Args:
            user_id:            Discord / investor user id.
            signal:             One of VALID_USER_SIGNALS.
            ticker:             Optional ticker context (e.g. VNM).
            interaction_log_id: ID of the AIInteractionLog that was reacted to.
            agent_type:         Denormalised agent type for fast queries.
            source:             Origin of the signal (default: discord_reaction).
            note:               Optional free-text note.

        Returns:
            True on success, False on any failure (fire-and-forget semantics).
        """
        if signal not in VALID_USER_SIGNALS:
            logger.warning(
                "memory_service.log_user_signal.invalid",
                signal=signal,
                valid=list(VALID_USER_SIGNALS),
            )
            return False

        try:
            from src.platform.db import AsyncSessionLocal  # noqa: PLC0415
            from src.ai.memory.user_behavior_log import UserBehaviorLog  # noqa: PLC0415

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # 1. Write UserBehaviorLog row
                    behavior = UserBehaviorLog(
                        user_id=user_id,
                        signal=signal,
                        source=source,
                        interaction_log_id=interaction_log_id,
                        ticker=ticker,
                        agent_type=agent_type,
                        note=note,
                    )
                    session.add(behavior)

                    # 2. Back-fill AIInteractionLog.user_signal (compat layer)
                    if interaction_log_id is not None:
                        repo = InteractionLogRepository(session)
                        log_row = await repo.get_by_id(interaction_log_id)
                        if log_row is not None and log_row.user_signal is None:
                            log_row.user_signal = signal
                            logger.debug(
                                "memory_service.log_user_signal.backfilled",
                                interaction_log_id=interaction_log_id,
                                signal=signal,
                            )

            logger.info(
                "memory_service.log_user_signal.ok",
                user_id=user_id,
                signal=signal,
                ticker=ticker,
                source=source,
            )
            return True

        except Exception as exc:
            logger.warning(
                "memory_service.log_user_signal.failed",
                user_id=user_id,
                signal=signal,
                error=str(exc),
            )
            return False

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

        Each query (episodes, snapshot) fails independently.
        Returns an empty MemoryContext (not None) when no data exists.
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
