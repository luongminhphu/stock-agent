"""
Self-Improvement Advisor (Wave 4) — core segment.

Three responsibilities:
  1. FailurePatternAnalyser   — pure, deterministic analysis of FeedbackStore data.
                               Zero AI cost. Always available as standalone utility.
  2. SelfImprovementAdvisor   — orchestrates: analyse → AI call → log suggestions.
                               Guardrail: requires_human_approval is ALWAYS True.
                               Never auto-applies any change.
  3. EvolutionStore           — ORM model + persistence layer for evolution_log.

Typical usage (weekly scheduled job):

    advisor = SelfImprovementAdvisor(ai_client=get_ai_client())
    suggestions = await advisor.analyse_and_suggest(days=30)
    # suggestions are persisted to evolution_log with status="pending"
    # owner reviews via Discord command or API; marks applied/dismissed

Table: evolution_log
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, select, update
from sqlalchemy.orm import Mapped, mapped_column

from src.core.feedback import EngineFeedback, FeedbackStore
from src.platform.db import AsyncSessionLocal, Base
from src.platform.logging import get_logger

logger = get_logger(__name__)


# ─── ORM model ──────────────────────────────────────────────────────────────────────

class EvolutionLog(Base):
    """One row per AI-suggested improvement.

    A single adviser run (one run_id) may produce multiple rows.
    Owner reviews each row independently and marks applied/dismissed.

    status lifecycle:
        pending   →  applied   (owner confirmed the change was made)
        pending   →  dismissed (owner rejected the suggestion)
    """
    __tablename__ = "evolution_log"

    id: Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str]       = mapped_column(String(64), nullable=False, index=True)
    target: Mapped[str]       = mapped_column(String(32), nullable=False)
    # prompt | signal_weight | dispatch_rule | schema | heuristic
    description: Mapped[str]  = mapped_column(Text, nullable=False)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proposed_change: Mapped[str]  = mapped_column(Text, nullable=False)
    risk_level: Mapped[str]   = mapped_column(String(8), nullable=False, default="low")
    status: Mapped[str]       = mapped_column(String(12), nullable=False, default="pending", index=True)
    # pending | applied | dismissed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(UTC),
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


# ─── analysis data structures ─────────────────────────────────────────────────────

@dataclass
class VerdictStats:
    """Accuracy metrics for a single verdict type."""
    verdict: str
    total: int                = 0
    correct: int              = 0
    incorrect: int            = 0
    partial: int              = 0
    not_acted: int            = 0
    avg_delta_score: float    = 0.0

    @property
    def accuracy(self) -> float:
        """correct / (correct + incorrect). Ignores partial and not_acted."""
        denominator = self.correct + self.incorrect
        return self.correct / denominator if denominator else 0.0

    @property
    def is_weak(self) -> bool:
        return self.accuracy < 0.5 and (self.correct + self.incorrect) >= 3


@dataclass
class TriggerSourceStats:
    """Accuracy metrics grouped by trigger_source."""
    trigger_source: str
    total: int             = 0
    incorrect_count: int   = 0
    avg_delta_score: float = 0.0

    @property
    def incorrect_rate(self) -> float:
        return self.incorrect_count / self.total if self.total else 0.0


@dataclass
class PatternReport:
    """Structured analysis output, serialised into the AI prompt."""
    period_days: int
    total_feedback: int
    weak_verdicts: list[VerdictStats]            = field(default_factory=list)
    dominant_bad_triggers: list[TriggerSourceStats] = field(default_factory=list)
    all_verdict_stats: list[VerdictStats]        = field(default_factory=list)
    overall_accuracy: float                      = 0.0
    generated_at: str                            = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass
class ImprovementSuggestion:
    """One actionable suggestion produced by SelfImprovementAdvisor.

    NEVER auto-applied. Always requires human approval.
    """
    target: Literal["prompt", "signal_weight", "dispatch_rule", "schema", "heuristic"]
    description: str
    evidence_summary: str
    proposed_change: str
    risk_level: Literal["low", "medium", "high"] = "low"
    requires_human_approval: bool = True  # immutable guardrail, always True

    def __post_init__(self) -> None:
        # Guardrail cannot be overridden
        object.__setattr__(self, "requires_human_approval", True)


# ─── FailurePatternAnalyser ──────────────────────────────────────────────────────────

class FailurePatternAnalyser:
    """Pure analysis layer. Zero AI cost. Deterministic.

    Usage::

        entries = await FeedbackStore.get_recent(days=30)
        report  = FailurePatternAnalyser.build_pattern_report(entries, days=30)
    """

    @staticmethod
    def aggregate_verdict_outcomes(
        entries: list[EngineFeedback],
    ) -> dict[str, VerdictStats]:
        """Group feedback by verdict type and compute accuracy metrics."""
        stats: dict[str, VerdictStats] = {}
        delta_accum: dict[str, list[float]] = defaultdict(list)

        for e in entries:
            if e.verdict not in stats:
                stats[e.verdict] = VerdictStats(verdict=e.verdict)
            s = stats[e.verdict]
            s.total += 1
            if e.outcome == "correct":
                s.correct += 1
            elif e.outcome == "incorrect":
                s.incorrect += 1
            elif e.outcome == "partial":
                s.partial += 1
            else:
                s.not_acted += 1
            delta_accum[e.verdict].append(e.delta_score)

        for verdict, deltas in delta_accum.items():
            stats[verdict].avg_delta_score = sum(deltas) / len(deltas) if deltas else 0.0

        return stats

    @staticmethod
    def find_weak_verdicts(
        stats: dict[str, VerdictStats],
        min_samples: int = 3,
    ) -> list[VerdictStats]:
        """Return verdicts with accuracy < 0.5 and at least min_samples evaluations."""
        return [
            s for s in stats.values()
            if (s.correct + s.incorrect) >= min_samples and s.accuracy < 0.5
        ]

    @staticmethod
    def find_dominant_trigger_sources(
        entries: list[EngineFeedback],
        incorrect_rate_threshold: float = 0.40,
        min_samples: int = 3,
    ) -> list[TriggerSourceStats]:
        """Return trigger sources with high incorrect rates."""
        acc: dict[str, TriggerSourceStats] = {}
        delta_accum: dict[str, list[float]] = defaultdict(list)

        for e in entries:
            src = e.trigger_source or "unknown"
            if src not in acc:
                acc[src] = TriggerSourceStats(trigger_source=src)
            t = acc[src]
            t.total += 1
            if e.outcome == "incorrect":
                t.incorrect_count += 1
            delta_accum[src].append(e.delta_score)

        for src, deltas in delta_accum.items():
            acc[src].avg_delta_score = sum(deltas) / len(deltas) if deltas else 0.0

        return [
            t for t in acc.values()
            if t.total >= min_samples and t.incorrect_rate >= incorrect_rate_threshold
        ]

    @classmethod
    def build_pattern_report(
        cls,
        entries: list[EngineFeedback],
        days: int,
    ) -> PatternReport:
        """Top-level: build the full PatternReport from raw feedback entries."""
        if not entries:
            return PatternReport(
                period_days=days,
                total_feedback=0,
                overall_accuracy=0.0,
            )

        verdict_stats = cls.aggregate_verdict_outcomes(entries)
        weak = cls.find_weak_verdicts(verdict_stats)
        bad_triggers = cls.find_dominant_trigger_sources(entries)

        correct_total  = sum(s.correct  for s in verdict_stats.values())
        assessed_total = sum(
            s.correct + s.incorrect for s in verdict_stats.values()
        )
        overall_accuracy = correct_total / assessed_total if assessed_total else 0.0

        return PatternReport(
            period_days=days,
            total_feedback=len(entries),
            weak_verdicts=weak,
            dominant_bad_triggers=bad_triggers,
            all_verdict_stats=list(verdict_stats.values()),
            overall_accuracy=overall_accuracy,
        )


# ─── EvolutionStore ────────────────────────────────────────────────────────────────────────

class EvolutionStore:
    """Persistence layer for EvolutionLog rows.

    Usage::

        rows = await EvolutionStore.save_suggestions(run_id, suggestions)
        pending = await EvolutionStore.get_pending()
        await EvolutionStore.mark_applied(row_id=pending[0].id)
    """

    @staticmethod
    async def save_suggestions(
        run_id: str,
        suggestions: list[ImprovementSuggestion],
    ) -> list[EvolutionLog]:
        """Batch-insert suggestions. Returns saved ORM rows."""
        rows = [
            EvolutionLog(
                run_id=run_id,
                target=s.target,
                description=s.description,
                evidence_summary=s.evidence_summary,
                proposed_change=s.proposed_change,
                risk_level=s.risk_level,
                status="pending",
            )
            for s in suggestions
        ]
        async with AsyncSessionLocal() as session:
            session.add_all(rows)
            await session.commit()
            for r in rows:
                await session.refresh(r)
        return rows

    @staticmethod
    async def get_pending() -> list[EvolutionLog]:
        """Return all pending suggestions, newest first."""
        async with AsyncSessionLocal() as session:
            stmt = (
                select(EvolutionLog)
                .where(EvolutionLog.status == "pending")
                .order_by(EvolutionLog.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    @staticmethod
    async def mark_applied(row_id: int) -> None:
        """Owner confirmed the suggestion was manually applied."""
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(EvolutionLog)
                .where(EvolutionLog.id == row_id)
                .values(status="applied", reviewed_at=datetime.now(UTC))
            )
            await session.commit()
        logger.info("evolution_store.marked_applied", row_id=row_id)

    @staticmethod
    async def mark_dismissed(row_id: int) -> None:
        """Owner rejected the suggestion."""
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(EvolutionLog)
                .where(EvolutionLog.id == row_id)
                .values(status="dismissed", reviewed_at=datetime.now(UTC))
            )
            await session.commit()
        logger.info("evolution_store.marked_dismissed", row_id=row_id)

    @staticmethod
    async def get_history(
        status: str | None = None,
        limit: int = 50,
    ) -> list[EvolutionLog]:
        """Return evolution log history, optionally filtered by status."""
        async with AsyncSessionLocal() as session:
            stmt = (
                select(EvolutionLog)
                .order_by(EvolutionLog.created_at.desc())
                .limit(limit)
            )
            if status:
                stmt = stmt.where(EvolutionLog.status == status)
            result = await session.execute(stmt)
            return list(result.scalars().all())


# ─── SelfImprovementAdvisor ───────────────────────────────────────────────────────────────

class SelfImprovementAdvisor:
    """Orchestrates: FeedbackStore → analyse → AI call → log suggestions.

    Main entry point: analyse_and_suggest()

    Guardrail:
        Every ImprovementSuggestion.requires_human_approval == True.
        No suggestion is ever auto-applied to any code path.
        Suggestions are persisted to evolution_log with status='pending'.
        Owner must explicitly call EvolutionStore.mark_applied(id).

    Minimum data gate:
        Skips AI call when feedback pool < MIN_FEEDBACK_FOR_AI.
        Falls back to heuristic suggestions from FailurePatternAnalyser.

    Args:
        ai_client: AsyncAIClient instance from src.ai.client.
                   When None, only heuristic suggestions are produced.
    """
    MIN_FEEDBACK_FOR_AI = 10
    MIN_WEAK_VERDICTS   = 1

    def __init__(self, ai_client: Any | None = None) -> None:
        self._ai_client = ai_client

    async def analyse_and_suggest(
        self,
        days: int = 30,
        user_id: str | None = None,
    ) -> list[ImprovementSuggestion]:
        """Full Wave 4 cycle.

        1. Load feedback from FeedbackStore.
        2. Build PatternReport (deterministic, zero AI cost).
        3. Skip if insufficient data.
        4. Call AI with PatternReport → get structured suggestions.
        5. Fall back to heuristic suggestions if AI unavailable.
        6. Persist all suggestions to EvolutionStore.
        7. Return suggestions (caller emits EvolutionSuggestionReadyEvent).

        Returns:
            List of ImprovementSuggestion. Empty list if no patterns found.
        """
        run_id = str(uuid4())
        logger.info("evolution.cycle_start", run_id=run_id, days=days)

        # Step 1: load feedback
        entries = await FeedbackStore.get_recent(days=days, user_id=user_id)
        logger.info("evolution.feedback_loaded", count=len(entries), run_id=run_id)

        if not entries:
            logger.info("evolution.no_feedback", run_id=run_id)
            return []

        # Step 2: build report
        report = FailurePatternAnalyser.build_pattern_report(entries, days=days)
        logger.info(
            "evolution.report_built",
            run_id=run_id,
            overall_accuracy=round(report.overall_accuracy, 3),
            weak_verdicts=len(report.weak_verdicts),
            bad_triggers=len(report.dominant_bad_triggers),
        )

        # Step 3: data gate
        has_enough_data = (
            report.total_feedback >= self.MIN_FEEDBACK_FOR_AI
            and len(report.weak_verdicts) >= self.MIN_WEAK_VERDICTS
        )

        suggestions: list[ImprovementSuggestion]

        if has_enough_data and self._ai_client is not None:
            # Step 4: AI path
            suggestions = await self._call_ai(report, run_id)
        else:
            # Step 5: heuristic fallback
            reason = "insufficient_data" if not has_enough_data else "no_ai_client"
            logger.info("evolution.heuristic_fallback", run_id=run_id, reason=reason)
            suggestions = self._heuristic_suggestions(report)

        if not suggestions:
            logger.info("evolution.no_suggestions", run_id=run_id)
            return []

        # Step 6: persist
        await EvolutionStore.save_suggestions(run_id, suggestions)
        logger.info(
            "evolution.suggestions_saved",
            run_id=run_id,
            count=len(suggestions),
        )
        return suggestions

    async def _call_ai(
        self,
        report: PatternReport,
        run_id: str,
    ) -> list[ImprovementSuggestion]:
        """Call AI with PatternReport. Returns parsed ImprovementSuggestion list."""
        from src.ai.prompts.evolution_advisor import (
            build_system_prompt,
            build_user_prompt,
            parse_ai_response,
        )

        system = build_system_prompt()
        user   = build_user_prompt(report)

        try:
            # Use chat_completion directly — evolution uses its own parse_ai_response()
            # and does NOT need response_schema parsing. Do NOT pass response_format:
            # sonar-pro rejects json_object (HTTP 400); JSON is enforced via prompt.
            import json as _json  # noqa: PLC0415
            import re as _re        # noqa: PLC0415
            json_instruction = (
                "You MUST respond with valid JSON only. "
                "No markdown, no code fences, no explanation outside JSON."
            )
            messages = [
                {"role": "system", "content": f"{json_instruction}\n\n{system}"},
                {"role": "user",   "content": user},
            ]
            response = await self._ai_client.chat_completion(messages=messages)
            raw_text = self._ai_client.extract_text(response)
            # Strip markdown code fences if model wraps output
            raw_text = _re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=_re.MULTILINE)
            raw_text = _re.sub(r"```\s*$", "", raw_text.strip(), flags=_re.MULTILINE)
            raw = _json.loads(raw_text.strip())
            suggestions = parse_ai_response(raw)
            logger.info(
                "evolution.ai_suggestions_parsed",
                run_id=run_id,
                count=len(suggestions),
            )
            return suggestions
        except Exception as exc:
            logger.error(
                "evolution.ai_call_failed",
                run_id=run_id,
                error=str(exc),
            )
            # graceful fallback to heuristic
            return self._heuristic_suggestions(report)

    @staticmethod
    def _heuristic_suggestions(
        report: PatternReport,
    ) -> list[ImprovementSuggestion]:
        """Produce minimal rule-based suggestions when AI is unavailable."""
        suggestions: list[ImprovementSuggestion] = []

        for vs in report.weak_verdicts:
            evidence = (
                f"Verdict '{vs.verdict}': accuracy={vs.accuracy:.0%} "
                f"(correct={vs.correct}, incorrect={vs.incorrect}) "
                f"over {vs.correct + vs.incorrect} evaluations."
            )
            suggestions.append(
                ImprovementSuggestion(
                    target="heuristic",
                    description=(
                        f"Verdict '{vs.verdict}' has low accuracy ({vs.accuracy:.0%}). "
                        "Consider raising the confidence threshold or revising "
                        "the signal weights that produce this verdict."
                    ),
                    evidence_summary=evidence,
                    proposed_change=(
                        f"Review _derive_verdict_heuristic() in engine.py. "
                        f"Increase confidence floor for '{vs.verdict}' verdict "
                        f"from current threshold or add an additional signal "
                        f"cross-check before emitting this verdict."
                    ),
                    risk_level="low",
                )
            )

        for ts in report.dominant_bad_triggers:
            evidence = (
                f"Trigger source '{ts.trigger_source}': "
                f"incorrect_rate={ts.incorrect_rate:.0%} "
                f"over {ts.total} evaluations."
            )
            suggestions.append(
                ImprovementSuggestion(
                    target="dispatch_rule",
                    description=(
                        f"Trigger source '{ts.trigger_source}' produces "
                        f"incorrect verdicts {ts.incorrect_rate:.0%} of the time. "
                        "Consider reducing dispatch priority or adding "
                        "a pre-filter for this source."
                    ),
                    evidence_summary=evidence,
                    proposed_change=(
                        f"In IntelligenceEngineListener._handle(), check "
                        f"event.trigger_source == '{ts.trigger_source}' and "
                        f"set priority='low' or require confidence > 0.70 "
                        f"before dispatching."
                    ),
                    risk_level="low",
                )
            )

        return suggestions
