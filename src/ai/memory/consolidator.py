"""MemoryConsolidator — weekly distillation of episodic → semantic memory.

Called by bot/scheduler.py (MemoryConsolidatorScheduler) every Sunday ~02:00.

Process:
  1. Load all AIInteractionLog rows for the past 7 days.
  2. Call AI with consolidation prompt.
  3. Persist a new MemorySnapshot.

Owner: ai segment.
Callers: bot/scheduler.py (adapter) — scheduler just calls .run(), no logic there.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel

from src.ai.memory.models import AIInteractionLog, MemorySnapshot
from src.ai.memory.prompts import CONSOLIDATION_SYSTEM_PROMPT, build_consolidation_prompt
from src.ai.memory.repository import InteractionLogRepository, MemorySnapshotRepository
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.ai.client import AIClient

logger = get_logger(__name__)

_MIN_EPISODES = 3  # Don't consolidate if fewer than this many interactions
_LOOKBACK_DAYS = 7


class ConsolidationOutput(BaseModel):
    """Structured output schema for the consolidation AI call."""

    behavioral_patterns: str | None = None
    cognitive_biases: str | None = None
    strengths: str | None = None
    blind_spots: str | None = None
    confidence_calibration: str | None = None


class MemoryConsolidator:
    """Distills recent episodic logs into a MemorySnapshot.

    Args:
        client:  Injected AIClient with retry/circuit-breaker.
        user_id: The investor's user ID to consolidate for.
    """

    def __init__(self, client: AIClient, user_id: str) -> None:
        self._client = client
        self._user_id = user_id

    async def run(self, session: AsyncSession) -> MemorySnapshot | None:
        """Execute one consolidation cycle. Returns the new snapshot or None.

        Returns None (does NOT raise) when:
        - Not enough episodes
        - AI call fails
        - Any DB error
        """
        now = datetime.now(tz=timezone.utc)
        period_start = now - timedelta(days=_LOOKBACK_DAYS)

        try:
            episode_repo = InteractionLogRepository(session)
            episodes = await episode_repo.get_since(
                user_id=self._user_id,
                since=period_start,
            )

            if len(episodes) < _MIN_EPISODES:
                logger.info(
                    "memory_consolidator.skip.not_enough_episodes",
                    user_id=self._user_id,
                    episode_count=len(episodes),
                    minimum=_MIN_EPISODES,
                )
                return None

            logger.info(
                "memory_consolidator.run.start",
                user_id=self._user_id,
                episode_count=len(episodes),
            )

            output: ConsolidationOutput = await self._client.chat(
                system_prompt=CONSOLIDATION_SYSTEM_PROMPT,
                user_prompt=build_consolidation_prompt(
                    episodes=episodes,
                    period_start=period_start.strftime("%Y-%m-%d"),
                    period_end=now.strftime("%Y-%m-%d"),
                ),
                response_schema=ConsolidationOutput,
            )

            snapshot = MemorySnapshot(
                user_id=self._user_id,
                period_start=period_start,
                period_end=now,
                behavioral_patterns=output.behavioral_patterns,
                cognitive_biases=output.cognitive_biases,
                strengths=output.strengths,
                blind_spots=output.blind_spots,
                confidence_calibration=output.confidence_calibration,
                episode_count=len(episodes),
                # NOTE: stores average confidence, not true prediction accuracy.
                # TODO: replace with real accuracy once portfolio segment tracks
                # trade outcomes (verdict vs actual price direction).
                verdict_accuracy=_compute_avg_confidence(episodes),
            )

            snapshot_repo = MemorySnapshotRepository(session)
            saved = await snapshot_repo.save(snapshot)
            await session.commit()

            logger.info(
                "memory_consolidator.run.done",
                user_id=self._user_id,
                snapshot_id=saved.id,
                episode_count=len(episodes),
            )
            return saved

        except Exception as exc:
            logger.error(
                "memory_consolidator.run.failed",
                user_id=self._user_id,
                error=str(exc),
            )
            return None


def _compute_avg_confidence(episodes: list[AIInteractionLog]) -> float | None:
    """Return average AI confidence across episodes that have a score.

    This is stored in MemorySnapshot.verdict_accuracy as a proxy metric.
    It reflects how certain the AI was — NOT whether verdicts were correct.

    TODO: replace with real accuracy (correct verdicts / total verdicts) once
    the portfolio segment records trade outcomes for closed positions.
    """
    with_confidence = [e for e in episodes if e.ai_confidence is not None]
    if not with_confidence:
        return None
    avg = sum(e.ai_confidence for e in with_confidence) / len(with_confidence)  # type: ignore[arg-type]
    return round(avg, 3)
