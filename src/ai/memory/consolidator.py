"""MemoryConsolidator — weekly distillation of episodic → semantic memory.

Called by bot/scheduler.py (MemoryConsolidatorScheduler) every Sunday ~02:00.

Process:
  1. Load all AIInteractionLog rows for the past 7 days.
  2. Call AI with consolidation prompt.
  3. Persist a new MemorySnapshot.

Wave 7 addition — synthesize_patterns() classmethod:
  - On-demand pattern extraction (not weekly-only).
  - Called by: ContextBuilder (before prompt inject), API /memory/patterns,
    bot command /memory refresh.
  - Returns PatternSynthesisOutput | None — never raises.
  - Groups episodes by agent_type for cleaner AI reasoning.
  - Stores result into MemorySnapshot for backward-compat with render().
  - Min 5 episodes guard (stricter than run()'s 3) to avoid hallucinated patterns.

Owner: ai segment.
Callers: bot/scheduler.py (adapter) — scheduler just calls .run(), no logic there.
         context_builder.py — calls synthesize_patterns() for fresh inject.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.ai.memory.models import AIInteractionLog, MemorySnapshot
from src.ai.memory.prompts import (
    CONSOLIDATION_SYSTEM_PROMPT,
    PATTERN_SYNTHESIS_SYSTEM_PROMPT,
    build_consolidation_prompt,
    build_pattern_synthesis_prompt,
)
from src.ai.memory.repository import InteractionLogRepository, MemorySnapshotRepository
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.ai.client import AIClient

logger = get_logger(__name__)

_MIN_EPISODES = 3       # weekly run threshold
_MIN_SYNTH_EPISODES = 5 # on-demand synthesis threshold (stricter)
_LOOKBACK_DAYS = 7


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ConsolidationOutput(BaseModel):
    """Structured output schema for the weekly consolidation AI call."""

    behavioral_patterns: str | None = None
    cognitive_biases: str | None = None
    strengths: str | None = None
    blind_spots: str | None = None
    confidence_calibration: str | None = None


class PatternSynthesisOutput(BaseModel):
    """Structured output for on-demand pattern synthesis (Wave 7).

    Designed for downstream injection into AI prompts via ContextBuilder.
    Key design decisions:
      - patterns: plain-language strings — directly injectable without transformation.
      - bias_warnings: "Condition X → you tend to Y" format for actionable prompts.
      - market_regime_reads: regime distribution as strings, e.g. ["RISK_ON x3"].
      - confidence: drives whether ContextBuilder injects or skips this block.
                   < 0.5 → skip (not enough signal), >= 0.5 → inject with note.
    """

    patterns: list[str] = Field(default_factory=list)
    bias_warnings: list[str] = Field(default_factory=list)
    market_regime_reads: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    def to_prompt_block(self) -> str:
        """Render as a compact memory context block for prompt injection.

        Returns empty string when confidence < 0.5 or no content —
        callers can safely concatenate without null-checking.
        """
        if self.confidence < 0.5 or (not self.patterns and not self.bias_warnings):
            return ""

        parts: list[str] = [f"[Investor memory — confidence={self.confidence:.0%}]"]

        if self.patterns:
            parts.append("Patterns:")
            for p in self.patterns:
                parts.append(f"  • {p}")

        if self.bias_warnings:
            parts.append("Bias warnings:")
            for w in self.bias_warnings:
                parts.append(f"  ⚠️ {w}")

        if self.market_regime_reads:
            parts.append(f"Regime history: {' | '.join(self.market_regime_reads)}")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Consolidator
# ---------------------------------------------------------------------------


class MemoryConsolidator:
    """Distills recent episodic logs into a MemorySnapshot.

    Args:
        client:  Injected AIClient with retry/circuit-breaker.
        user_id: The investor's user ID to consolidate for.
    """

    def __init__(self, client: AIClient, user_id: str) -> None:
        self._client = client
        self._user_id = user_id

    # ------------------------------------------------------------------
    # Weekly run (existing — unchanged)
    # ------------------------------------------------------------------

    async def run(self, session: AsyncSession) -> MemorySnapshot | None:
        """Execute one weekly consolidation cycle. Returns the new snapshot or None.

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

    # ------------------------------------------------------------------
    # On-demand pattern synthesis (Wave 7)
    # ------------------------------------------------------------------

    async def synthesize_patterns(
        self,
        session: AsyncSession,
        lookback_days: int = 14,
    ) -> PatternSynthesisOutput | None:
        """On-demand pattern synthesis from recent episodic memory.

        Distinct from run():
          - run():                weekly cadence, writes MemorySnapshot to DB.
          - synthesize_patterns(): any cadence, returns PatternSynthesisOutput
                                   in-memory for immediate prompt injection.
                                   Also persists result as MemorySnapshot
                                   so ContextBuilder.render() stays backward-compat.

        Args:
            session:       Active AsyncSession.
            lookback_days: Episode window. Default 14 (wider than weekly run)
                           so patterns have more signal to detect.

        Returns:
            PatternSynthesisOutput | None.
            None when:
              - Not enough episodes (< _MIN_SYNTH_EPISODES = 5)
              - AI call fails
              - Any DB error
            Never raises.

        Caller contract:
            result = await consolidator.synthesize_patterns(session)
            if result:
                prompt_block = result.to_prompt_block()  # inject into system prompt
                # result.confidence < 0.5 → to_prompt_block() returns "" automatically
        """
        now = datetime.now(tz=timezone.utc)
        period_start = now - timedelta(days=lookback_days)
        period_label = f"{period_start.strftime('%Y-%m-%d')} → {now.strftime('%Y-%m-%d')}"

        try:
            episode_repo = InteractionLogRepository(session)
            episodes = await episode_repo.get_since(
                user_id=self._user_id,
                since=period_start,
            )

            if len(episodes) < _MIN_SYNTH_EPISODES:
                logger.info(
                    "memory_consolidator.synthesize.skip",
                    user_id=self._user_id,
                    episode_count=len(episodes),
                    minimum=_MIN_SYNTH_EPISODES,
                )
                return None

            logger.info(
                "memory_consolidator.synthesize.start",
                user_id=self._user_id,
                episode_count=len(episodes),
                lookback_days=lookback_days,
            )

            output: PatternSynthesisOutput = await self._client.chat(
                system_prompt=PATTERN_SYNTHESIS_SYSTEM_PROMPT,
                user_prompt=build_pattern_synthesis_prompt(
                    episodes=episodes,
                    period_label=period_label,
                ),
                response_schema=PatternSynthesisOutput,
            )

            # Persist as MemorySnapshot so ContextBuilder.render() stays
            # backward-compat with existing latest_snapshot.as_context_block().
            # Store the pattern list + bias_warnings as behavioral_patterns JSON.
            import json
            snapshot = MemorySnapshot(
                user_id=self._user_id,
                period_start=period_start,
                period_end=now,
                behavioral_patterns=json.dumps(
                    {
                        "patterns": output.patterns,
                        "bias_warnings": output.bias_warnings,
                        "market_regime_reads": output.market_regime_reads,
                        "confidence": output.confidence,
                    },
                    ensure_ascii=False,
                ),
                cognitive_biases=None,
                strengths=None,
                blind_spots=None,
                confidence_calibration=None,
                episode_count=len(episodes),
                verdict_accuracy=_compute_avg_confidence(episodes),
            )
            snapshot_repo = MemorySnapshotRepository(session)
            await snapshot_repo.save(snapshot)
            await session.commit()

            logger.info(
                "memory_consolidator.synthesize.done",
                user_id=self._user_id,
                patterns=len(output.patterns),
                bias_warnings=len(output.bias_warnings),
                confidence=output.confidence,
            )
            return output

        except Exception as exc:
            logger.error(
                "memory_consolidator.synthesize.failed",
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
