"""InvestorProfile — persistent self-knowledge layer for AI context injection.

Owner: platform segment.

Builds a daily snapshot from DecisionLog + Thesis + portfolio state.
Consumed by ai.ContextBuilder (Wave 2) — not by domain services directly.

Two layers:
  StaticProfile     : read from settings (.env), edited manually by owner.
  InvestorProfileSnapshot : auto-built each morning from DB, stored in investor_profiles table.

Usage:
    # In bootstrap or scheduler:
    async with AsyncSessionLocal() as session:
        svc = InvestorProfileService(session)
        snapshot = await svc.build_snapshot(user_id=settings.scheduler_user_id)
        await session.commit()

    # In ContextBuilder (Wave 2):
    context = await svc.get_investor_context()
    prompt += context.to_prompt_block()
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass

from sqlalchemy import DateTime, Float, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.platform.db import Base
from src.platform.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# ORM Model
# ---------------------------------------------------------------------------


class InvestorProfileSnapshot(Base):
    """Daily snapshot — rebuilt every morning, keeps full history for trend analysis."""

    __tablename__ = "investor_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    snapshot_date: Mapped[datetime.date] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=datetime.date.today,
    )

    # Behavioral insights extracted from DecisionLog
    behavioral_patterns: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[str]
    confirmed_biases: Mapped[str] = mapped_column(Text, default="[]")     # JSON list[str]
    top_lessons: Mapped[str] = mapped_column(Text, default="[]")          # JSON list[str], top 5

    # Portfolio state snapshot
    portfolio_bias: Mapped[str] = mapped_column(String(512), default="")  # e.g. "Banking 42%, RE 18%"
    active_thesis_count: Mapped[int] = mapped_column(Integer, default=0)

    # Decision performance metrics
    win_rate_30d: Mapped[float] = mapped_column(Float, default=0.0)   # % correct decisions
    avg_hold_days: Mapped[float] = mapped_column(Float, default=0.0)  # avg holding period

    # AI-ready narrative block — pre-rendered for fast injection
    summary_for_ai: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Static Profile (from settings)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StaticProfile:
    """Immutable config from .env — owner edits manually when investment style changes."""

    risk_appetite: str        # "medium — max drawdown 10%, position size ≤15%"
    thesis_style: str         # "fundamental, hold 3-6 months"
    trading_horizon: str      # "swing to positional — no day trading"
    preferred_sectors: str    # "banking, consumer staples, tech"
    avoid: str                # "speculative penny stocks, T+ illiquid"

    @classmethod
    def from_settings(cls) -> "StaticProfile":
        """Build StaticProfile from platform settings singleton."""
        from src.platform.config import settings

        return cls(
            risk_appetite=settings.investor_risk_appetite,
            thesis_style=settings.investor_thesis_style,
            trading_horizon=settings.investor_trading_horizon,
            preferred_sectors=settings.investor_preferred_sectors,
            avoid=settings.investor_avoid,
        )


# ---------------------------------------------------------------------------
# Combined DTO — what ContextBuilder (Wave 2) actually consumes
# ---------------------------------------------------------------------------


@dataclass
class InvestorContext:
    """Full investor context — passed to AI agents as enriched prompt context.

    Immutable after creation. Wave 2 ContextBuilder builds this once per session
    and passes it to every agent call that benefits from investor self-knowledge.
    """

    static: StaticProfile
    snapshot: InvestorProfileSnapshot | None  # None on first boot before any snapshot exists

    def to_prompt_block(self) -> str:
        """Render as compact plain-text block for injection into AI system prompts.

        Designed to be concise (<300 chars in normal operation) so it doesn't
        crowd out ticker-specific context in the prompt.
        """
        lines = [
            "=== INVESTOR PROFILE ===",
            f"Risk: {self.static.risk_appetite}",
            f"Style: {self.static.thesis_style} | Horizon: {self.static.trading_horizon}",
            f"Focus: {self.static.preferred_sectors} | Avoid: {self.static.avoid}",
        ]

        if self.snapshot:
            s = self.snapshot

            if s.active_thesis_count:
                lines.append(f"Active theses: {s.active_thesis_count}")

            if s.win_rate_30d:
                lines.append(
                    f"Win rate (30d): {s.win_rate_30d:.0f}%  |  Avg hold: {s.avg_hold_days:.0f}d"
                )

            if s.portfolio_bias:
                lines.append(f"Portfolio: {s.portfolio_bias}")

            patterns = _parse_json_list(s.behavioral_patterns)
            if patterns:
                lines.append(f"Patterns: {'; '.join(patterns[:2])}")

            lessons = _parse_json_list(s.top_lessons)
            if lessons:
                lines.append(f"Last lesson: {lessons[0]}")

        lines.append("========================")
        return "\n".join(lines)

    def is_empty(self) -> bool:
        """True if no meaningful snapshot data exists yet (first boot)."""
        return self.snapshot is None


# ---------------------------------------------------------------------------
# InvestorProfileService
# ---------------------------------------------------------------------------


class InvestorProfileService:
    """Build and retrieve InvestorProfile snapshots.

    Called by:
        platform.bootstrap  — load latest snapshot on startup
        bot.scheduler       — rebuild snapshot at 08:20 daily (before morning brief)
        ai.ContextBuilder   — Wave 2: get current snapshot for prompt injection

    Never called by domain services (thesis, watchlist, briefing, market).
    Dependency direction: platform ← thesis/portfolio (read-only imports inside methods).
    """

    def __init__(self, session) -> None:  # AsyncSession typed loosely to avoid circular import
        self._session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_latest(self) -> InvestorProfileSnapshot | None:
        """Return the most recent snapshot, or None if none exists yet."""
        result = await self._session.execute(
            select(InvestorProfileSnapshot)
            .order_by(InvestorProfileSnapshot.snapshot_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_investor_context(self) -> InvestorContext:
        """Return combined static + dynamic context. Safe to call before first snapshot."""
        static = StaticProfile.from_settings()
        snapshot = await self.get_latest()
        return InvestorContext(static=static, snapshot=snapshot)

    async def get_profile(self, user_id: str | None = None) -> dict:
        """Return investor profile as a dict compatible with ContextBuilder._apply_profile().

        Wave 3: this is the contract method called by ai.ContextBuilder.
        Combines StaticProfile (from settings) with the latest snapshot's
        behavioral notes and summary.

        Args:
            user_id: Accepted for API compatibility with ContextBuilder; currently
                     InvestorProfileSnapshot is single-tenant (one per system).
                     Will be used for multi-tenant filtering in a future wave.

        Returns:
            dict with keys: risk_appetite, avoid_list, preferred_sectors,
                            trading_style, notes.
            Returns {} on any failure so callers can safely treat as no-op.
        """
        try:
            static = StaticProfile.from_settings()
            snapshot = await self.get_latest()

            # avoid_list: split comma-separated avoid string from settings
            avoid_list: list[str] = [
                s.strip() for s in static.avoid.split(",") if s.strip()
            ]

            # preferred_sectors: split comma-separated string from settings
            preferred_sectors: list[str] = [
                s.strip() for s in static.preferred_sectors.split(",") if s.strip()
            ]

            # notes: combine snapshot summary_for_ai with win_rate/avg_hold if available
            notes_parts: list[str] = []
            if snapshot and snapshot.summary_for_ai:
                notes_parts.append(snapshot.summary_for_ai)
            if snapshot and snapshot.win_rate_30d:
                notes_parts.append(
                    f"Win rate 30d: {snapshot.win_rate_30d:.0f}% | "
                    f"Avg hold: {snapshot.avg_hold_days:.0f}d"
                )

            return {
                "risk_appetite": static.risk_appetite,
                "avoid_list": avoid_list,
                "preferred_sectors": preferred_sectors,
                "trading_style": f"{static.thesis_style} | {static.trading_horizon}",
                "notes": " ".join(notes_parts),
            }
        except Exception as exc:
            logger.warning(
                "platform.investor_profile.get_profile_failed",
                user_id=user_id,
                error=str(exc),
            )
            return {}

    async def build_snapshot(self, user_id: str) -> InvestorProfileSnapshot:
        """Build today's snapshot from live DB data and persist it.

        Sources (dependency order):
            1. thesis.models.Thesis      — active thesis count
            2. thesis.models.DecisionLog — lessons, win rate, avg hold, patterns
            3. portfolio.models.Position — portfolio sector bias

        Contract:
            - Never calls AI — pure data aggregation.
            - Creates a new row each day (keeps history).
            - Caller is responsible for session.commit() after this returns.
            - Gracefully handles missing portfolio/thesis data (returns zeros).
        """
        today = datetime.datetime.now(datetime.timezone.utc)

        active_thesis_count = await self._count_active_theses(user_id)
        lessons, patterns, biases, win_rate, avg_hold = await self._extract_decision_insights(
            user_id
        )
        portfolio_bias = await self._summarize_portfolio_bias(user_id)

        summary = self._compose_summary(
            patterns=patterns,
            lessons=lessons,
            win_rate=win_rate,
            avg_hold=avg_hold,
            portfolio_bias=portfolio_bias,
            active_thesis_count=active_thesis_count,
        )

        snapshot = InvestorProfileSnapshot(
            snapshot_date=today,
            behavioral_patterns=json.dumps(patterns, ensure_ascii=False),
            confirmed_biases=json.dumps(biases, ensure_ascii=False),
            top_lessons=json.dumps(lessons, ensure_ascii=False),
            portfolio_bias=portfolio_bias,
            active_thesis_count=active_thesis_count,
            win_rate_30d=win_rate,
            avg_hold_days=avg_hold,
            summary_for_ai=summary,
        )
        self._session.add(snapshot)

        logger.info(
            "platform.investor_profile.snapshot_built",
            user_id=user_id,
            active_thesis_count=active_thesis_count,
            win_rate_30d=win_rate,
            avg_hold_days=avg_hold,
        )
        return snapshot

    # ------------------------------------------------------------------
    # Private aggregation helpers
    # ------------------------------------------------------------------

    async def _count_active_theses(self, user_id: str) -> int:
        try:
            from src.thesis.models import Thesis, ThesisStatus

            result = await self._session.execute(
                select(Thesis).where(
                    Thesis.user_id == user_id,
                    Thesis.status == ThesisStatus.ACTIVE,
                )
            )
            return len(result.scalars().all())
        except Exception as exc:
            logger.warning("platform.investor_profile.thesis_count_failed", error=str(exc))
            return 0

    async def _extract_decision_insights(
        self, user_id: str
    ) -> tuple[list[str], list[str], list[str], float, float]:
        """Return (lessons, patterns, biases, win_rate_30d, avg_hold_days)."""
        try:
            from src.thesis.models import DecisionLog, OutcomeVerdict

            cutoff_30d = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)

            result = await self._session.execute(
                select(DecisionLog)
                .where(DecisionLog.user_id == user_id)
                .order_by(DecisionLog.decision_at.desc())
                .limit(50)
            )
            decisions = result.scalars().all()

            # Top lessons (most recent with key_lesson set)
            lessons: list[str] = [
                d.key_lesson
                for d in decisions
                if d.key_lesson and d.key_lesson.strip()
            ][:5]

            # Unique patterns (preserve insertion order via dict)
            patterns: list[str] = list(
                dict.fromkeys(
                    d.pattern_detected
                    for d in decisions
                    if d.pattern_detected and d.pattern_detected.strip()
                )
            )[:5]

            # Biases — simple heuristic: repeated patterns become confirmed biases
            biases: list[str] = [
                f"Pattern lặp lại: {p}" for p in patterns[:3]
            ]

            # Win rate (last 30 days, evaluated decisions only)
            recent_evaluated = [
                d for d in decisions
                if d.decision_at and d.decision_at >= cutoff_30d
                and d.outcome_verdict is not None
            ]
            if recent_evaluated:
                correct = sum(
                    1 for d in recent_evaluated
                    if d.outcome_verdict == OutcomeVerdict.CORRECT
                )
                win_rate = (correct / len(recent_evaluated)) * 100
            else:
                win_rate = 0.0

            # Avg hold days (last 10 closed decisions)
            closed = [
                d for d in decisions
                if d.outcome_evaluated_at is not None and d.decision_at is not None
            ][:10]
            if closed:
                holds = [
                    (d.outcome_evaluated_at - d.decision_at).days
                    for d in closed
                    if (d.outcome_evaluated_at - d.decision_at).days >= 0
                ]
                avg_hold = sum(holds) / len(holds) if holds else 0.0
            else:
                avg_hold = 0.0

            return lessons, patterns, biases, win_rate, avg_hold

        except Exception as exc:
            logger.warning(
                "platform.investor_profile.decision_insights_failed", error=str(exc)
            )
            return [], [], [], 0.0, 0.0

    async def _summarize_portfolio_bias(self, user_id: str) -> str:
        """Return short string like 'Banking 42%, Real Estate 18%'.

        Attempts to read from portfolio.models.Position.
        Returns empty string gracefully if portfolio segment is unavailable.
        """
        try:
            from src.portfolio.models import Position

            result = await self._session.execute(
                select(Position).where(
                    Position.user_id == user_id,
                    Position.is_open.is_(True),
                )
            )
            positions = result.scalars().all()
            if not positions:
                return ""

            sector_totals: dict[str, float] = {}
            total_value = 0.0
            for pos in positions:
                sector = getattr(pos, "sector", None) or "Unknown"
                value = float(getattr(pos, "market_value", 0) or 0)
                sector_totals[sector] = sector_totals.get(sector, 0.0) + value
                total_value += value

            if total_value == 0:
                return ""

            top_sectors = sorted(sector_totals.items(), key=lambda x: x[1], reverse=True)[:3]
            parts = [
                f"{s} {(v / total_value) * 100:.0f}%"
                for s, v in top_sectors
                if v > 0
            ]
            return ", ".join(parts)

        except Exception as exc:
            logger.warning(
                "platform.investor_profile.portfolio_bias_failed", error=str(exc)
            )
            return ""

    def _compose_summary(  # noqa: PLR0913
        self,
        patterns: list[str],
        lessons: list[str],
        win_rate: float,
        avg_hold: float,
        portfolio_bias: str,
        active_thesis_count: int,
    ) -> str:
        """Compose a short AI-friendly narrative summary from aggregated data."""
        parts: list[str] = []

        if active_thesis_count:
            parts.append(f"{active_thesis_count} thesis đang active.")
        if win_rate:
            parts.append(f"Win rate 30 ngày: {win_rate:.0f}%.")
        if avg_hold:
            parts.append(f"Avg hold: {avg_hold:.0f} ngày.")
        if portfolio_bias:
            parts.append(f"Portfolio: {portfolio_bias}.")
        if patterns:
            parts.append(f"Pattern lặp lại: {patterns[0]}.")
        if lessons:
            parts.append(f"Lesson gần nhất: {lessons[0]}")

        return " ".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json_list(value: str) -> list[str]:
    """Safely parse a JSON-encoded list from DB text field."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, ValueError):
        return []
