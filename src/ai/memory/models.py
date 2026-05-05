"""ORM models for the ai.memory sub-module.

Layer 2 — AIInteractionLog: one row per AI call.
Layer 3 — MemorySnapshot:   one row per weekly consolidation per user.

Owner: ai segment.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.db import Base


class AIInteractionLog(Base):
    """Layer 2 — Episodic Memory.

    Records every AI agent call with its input context snapshot and
    the structured verdict returned. Used by:
    - MemoryService.get_recent_episodes()   → feeds ContextBuilder
    - Consolidator.run()                    → distilled into MemorySnapshot
    """

    __tablename__ = "ai_interaction_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Which agent produced this entry
    agent_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="briefing | pretrade | replay | watchdog | thesis_review | suggest",
    )
    # What triggered the call (scheduled / manual / command name)
    trigger: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")

    # Tickers that were the subject of this interaction (JSON list)
    tickers_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="JSON list of ticker symbols, e.g. '[\"VCB\", \"VNM\"]'",
    )

    # AI output — stored as plain text summaries (not raw JSON blobs)
    ai_verdict: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="e.g. BULLISH / BEARISH / NEUTRAL / GO / NO_GO / HOLD",
    )
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_key_points: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Top 3-5 insights from AI, newline-separated. Not raw JSON.",
    )
    ai_risk_signals: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Risk signals highlighted by AI, newline-separated.",
    )

    # Optional FK linkage for traceability
    thesis_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    decision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def tickers(self) -> list[str]:
        if not self.tickers_json:
            return []
        try:
            return json.loads(self.tickers_json)
        except (ValueError, TypeError):
            return []

    @tickers.setter
    def tickers(self, value: list[str]) -> None:
        self.tickers_json = json.dumps(value)

    def __repr__(self) -> str:
        return (
            f"<AIInteractionLog id={self.id} user={self.user_id!r} "
            f"agent={self.agent_type!r} verdict={self.ai_verdict!r}>"
        )


class MemorySnapshot(Base):
    """Layer 3 — Semantic Memory.

    A weekly AI-distilled summary of the investor's behavioral patterns,
    cognitive biases, strengths, and blind spots. Built by Consolidator.
    Consumed by ContextBuilder to personalise every AI call.
    """

    __tablename__ = "memory_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # AI-distilled fields — plain prose, written by the consolidation prompt
    behavioral_patterns: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Recurring decision patterns, e.g. 'FOMO khi volume breakout'",
    )
    cognitive_biases: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Detected biases, e.g. 'Confirmation bias với banking sector'",
    )
    strengths: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Consistent strengths, e.g. 'Kỷ luật SL khi có plan sẵn'",
    )
    blind_spots: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Recurring blind spots, e.g. 'Bỏ qua macro khi local thesis quá mạnh'",
    )
    confidence_calibration: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="How well-calibrated confidence scores are vs actual outcomes",
    )

    # How many episodes were used to build this snapshot
    episode_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Raw quality signal: ratio of correct verdicts in the period
    verdict_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return (
            f"<MemorySnapshot id={self.id} user={self.user_id!r} "
            f"episodes={self.episode_count} created={self.created_at}>"
        )

    def as_context_block(self) -> str:
        """Render snapshot as a text block for injection into AI prompts.

        Returns empty string if no meaningful content is present.
        """
        lines: list[str] = []
        if self.behavioral_patterns:
            lines.append(f"Behavioral patterns: {self.behavioral_patterns}")
        if self.cognitive_biases:
            lines.append(f"Cognitive biases: {self.cognitive_biases}")
        if self.strengths:
            lines.append(f"Strengths: {self.strengths}")
        if self.blind_spots:
            lines.append(f"Blind spots: {self.blind_spots}")
        if self.confidence_calibration:
            lines.append(f"Confidence calibration: {self.confidence_calibration}")
        if not lines:
            return ""
        header = (
            f"[Memory snapshot — {self.episode_count} interactions, "
            f"period: {self.period_start.date()} → {self.period_end.date()}]"
        )
        return header + "\n" + "\n".join(lines)
