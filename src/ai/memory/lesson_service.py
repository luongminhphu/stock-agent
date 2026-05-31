"""LessonService — persist and query replay-derived lessons.

Owner: ai segment.
Callers:
  - ai/agents/replay_agent.py  → persist_replay()
  - bot/commands (future)      → get_pattern_summary() for /stats command
  - briefing context builder   → get_pattern_summary() for brief personalization

Write path:
  ReplayAgent calls persist_replay(record) after every completed replay run.
  Two rows are written atomically in an isolated session:
    1. AIInteractionLog(agent_type='replay_lesson') — lesson text + pattern
       tag; surfaced in MemoryContext.render() under [Recent AI interactions]
    2. UserBehaviorLog(signal='sold', source='replay') — explicit sold
       signal so pattern synthesis consolidator counts exits correctly

Read path:
  get_pattern_summary(user_id) → PatternCounter dataclass
    consumed by briefing context builder to personalize morning brief
    (e.g. "You have 3 early_exit patterns in the last 30 days — consider
    holding longer on high-conviction setups").

Boundary rules:
  - MUST NOT import from src.portfolio.* (no ORM models crossing the line).
  - Receives only ReplayOutcomeRecord (ai schema dataclass) — no raw Trade rows.
  - All write errors are swallowed; returns None / False on failure.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from src.ai.memory.models import AIInteractionLog
from src.ai.memory.repository import InteractionLogRepository
from src.ai.memory.user_behavior_log import UserBehaviorLog
from src.ai.schemas.replay import PatternTag, ReplayOutcomeRecord
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

_AGENT_TYPE = "replay_lesson"
_LESSON_LOOKBACK_DAYS = 90


@dataclass
class PatternCounter:
    """Aggregated behavioral pattern stats for one user.

    Returned by get_pattern_summary() and consumed by briefing
    context builder to inject a personalized behavior note into
    the morning brief prompt.

    win_rate: float 0-1 across all replay lessons in the lookback period.
    dominant_pattern: the PatternTag with the highest count, or None.
    counts: raw per-tag count dict, e.g. {PatternTag.EARLY_EXIT: 3, ...}.
    total: total replay lessons analyzed in the lookback period.
    """

    user_id: str
    total: int = 0
    win_count: int = 0
    win_rate: float = 0.0
    dominant_pattern: PatternTag | None = None
    counts: dict[str, int] = field(default_factory=dict)
    as_of: datetime = field(default_factory=lambda: datetime.now(UTC))

    def format_for_prompt(self) -> str:
        """Render a short behavior note for injection into morning brief.

        Returns empty string when no data is available (no lessons yet).

        Example output::

            [Behavior Patterns — last 90 days, 5 replays analyzed]
            Win rate: 60% | Dominant pattern: early_exit (3x)
            Watch: Bạn có xu hướng thoát lệnh sớm trước khi thesis hoàn toàn diễn ra.
        """
        if self.total == 0:
            return ""

        lines = [f"[Behavior Patterns \u2014 last {_LESSON_LOOKBACK_DAYS} days, {self.total} replays analyzed]"]
        win_pct = f"{self.win_rate:.0%}"
        dom = self.dominant_pattern.value if self.dominant_pattern else "none"
        lines.append(f"Win rate: {win_pct} | Dominant pattern: {dom} ({self.counts.get(dom, 0)}x)")

        _PATTERN_WARNINGS: dict[str, str] = {
            PatternTag.FOMO_ENTRY: "B\u1ea1n c\u00f3 xu h\u01b0\u1edbng v\u00e0o l\u1ec7nh sau khi gi\u00e1 \u0111\u00e3 b\u1ee9t ph\u00e1 \u2014 ki\u1ec3m tra l\u1ea1i entry discipline.",
            PatternTag.EARLY_EXIT: "B\u1ea1n c\u00f3 xu h\u01b0\u1edbng tho\u00e1t l\u1ec7nh s\u1edbm tr\u01b0\u1edbc khi thesis ho\u00e0n to\u00e0n di\u1ec5n ra.",
            PatternTag.IGNORED_STOP_LOSS: "B\u1ea1n \u0111\u00e3 gi\u1eef qua m\u1ee9c stop_loss nhi\u1ec1u l\u1ea7n \u2014 r\u1ee7i ro t\u1eadp trung cao.",
            PatternTag.THESIS_DRIFT: "Thesis th\u01b0\u1eddng b\u1ecb drift kh\u00f4ng c\u00f3 l\u00fd do r\u00f5 \u2014 re-validate tr\u01b0\u1edbc m\u1ed7i add.",
            PatternTag.OVERSIZED: "V\u1ecb th\u1ebf th\u01b0\u1eddng qu\u00e1 l\u1edbn so v\u1edbi conviction \u2014 scale size xu\u1ed1ng.",
            PatternTag.CORRECT_CONVICTION: "B\u1ea1n gi\u1eef \u0111\u01b0\u1ee3c discipline t\u1ed1t khi thesis \u0111\u00fang. Ti\u1ebfp t\u1ee5c.",
            PatternTag.SIZED_CORRECTLY: "Sizing \u0111\u01b0\u1ee3c ki\u1ec3m so\u00e1t t\u1ed1t. Ti\u1ebfp t\u1ee5c.",
        }

        if self.dominant_pattern and self.dominant_pattern in _PATTERN_WARNINGS:
            lines.append(f"Watch: {_PATTERN_WARNINGS[self.dominant_pattern]}")

        return "\n".join(lines)


class LessonService:
    """Stateless service \u2014 all methods are static, session created internally."""

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    @staticmethod
    async def persist_replay(record: ReplayOutcomeRecord) -> bool:
        """Persist a ReplayOutcomeRecord as two memory rows.

        Writes atomically in an isolated session (same pattern as
        MemoryService.log_interaction).

        Rows written:
          1. AIInteractionLog(agent_type='replay_lesson')
             ai_verdict    = outcome_verdict value
             ai_confidence = 1.0 (replay is factual, not probabilistic)
             ai_key_points = lessons joined as bullet text
             ai_risk_signals = pattern_tag + exit_reason_assessment
             tickers       = [record.ticker]
          2. UserBehaviorLog(signal='sold', source='replay')
             ticker        = record.ticker
             agent_type    = 'replay_lesson'
             note          = record.summary (first 512 chars)

        Returns True on success, False on any failure.
        """
        try:
            from src.platform.db import AsyncSessionLocal  # noqa: PLC0415

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    # 1. Build lesson text
                    lesson_bullets = "\n".join(
                        f"\u2022 {l}" for l in record.lessons
                    ) if record.lessons else "(no lessons extracted)"

                    risk_parts: list[str] = []
                    if record.pattern_tag:
                        risk_parts.append(f"pattern: {record.pattern_tag.value}")
                    if record.exit_reason_assessment:
                        risk_parts.append(f"exit assessment: {record.exit_reason_assessment}")
                    risk_text = " | ".join(risk_parts) or None

                    # 2. Write AIInteractionLog row
                    log_row = AIInteractionLog(
                        user_id=record.user_id,
                        agent_type=_AGENT_TYPE,
                        trigger="replay",
                        ai_verdict=record.outcome_verdict.value,
                        ai_confidence=1.0,
                        ai_key_points=lesson_bullets,
                        ai_risk_signals=risk_text,
                    )
                    log_row.tickers = [record.ticker]
                    repo = InteractionLogRepository(session)
                    saved_log = await repo.save(log_row)

                    # 3. Write UserBehaviorLog row
                    behavior = UserBehaviorLog(
                        user_id=record.user_id,
                        signal="sold",
                        source="replay",
                        interaction_log_id=saved_log.id if saved_log else None,
                        ticker=record.ticker,
                        agent_type=_AGENT_TYPE,
                        note=(record.summary[:512] if record.summary else None),
                    )
                    session.add(behavior)

            logger.info(
                "lesson_service.persist_replay.ok",
                user_id=record.user_id,
                ticker=record.ticker,
                verdict=record.outcome_verdict,
                pattern_tag=record.pattern_tag,
                trade_id=record.trade_id,
            )
            return True

        except Exception as exc:
            logger.warning(
                "lesson_service.persist_replay.failed",
                user_id=record.user_id if record else "unknown",
                error=str(exc),
            )
            return False

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    @staticmethod
    async def get_pattern_summary(
        session: AsyncSession,
        user_id: str,
        lookback_days: int = _LESSON_LOOKBACK_DAYS,
    ) -> PatternCounter:
        """Aggregate behavioral pattern stats for one user.

        Queries AIInteractionLog rows of agent_type='replay_lesson'
        within the lookback window.

        Used by:
          - briefing context builder to inject pattern note into brief prompt
          - /stats command (bot) to show investor their own patterns

        Returns an empty PatternCounter (total=0) when no lessons exist yet.
        """
        since = datetime.now(UTC) - timedelta(days=lookback_days)

        try:
            result = await session.execute(
                select(AIInteractionLog)
                .where(
                    AIInteractionLog.user_id == user_id,
                    AIInteractionLog.agent_type == _AGENT_TYPE,
                    AIInteractionLog.created_at >= since,
                )
                .order_by(AIInteractionLog.created_at.desc())
            )
            rows: list[AIInteractionLog] = list(result.scalars().all())
        except Exception as exc:
            logger.warning(
                "lesson_service.get_pattern_summary.query_failed",
                user_id=user_id,
                error=str(exc),
            )
            return PatternCounter(user_id=user_id)

        if not rows:
            return PatternCounter(user_id=user_id)

        win_values = {"WIN"}
        win_count = sum(1 for r in rows if r.ai_verdict in win_values)
        total = len(rows)
        win_rate = win_count / total if total else 0.0

        # Extract pattern_tag from ai_risk_signals field
        # Format written by persist_replay: "pattern: <tag> | exit assessment: ..."
        tag_counter: Counter[str] = Counter()
        for row in rows:
            if row.ai_risk_signals and "pattern: " in row.ai_risk_signals:
                raw = row.ai_risk_signals.split("pattern: ", 1)[1].split(" | ")[0].strip()
                if raw:
                    tag_counter[raw] += 1

        dominant_raw = tag_counter.most_common(1)[0][0] if tag_counter else None
        dominant: PatternTag | None = None
        if dominant_raw:
            try:
                dominant = PatternTag(dominant_raw)
            except ValueError:
                pass

        return PatternCounter(
            user_id=user_id,
            total=total,
            win_count=win_count,
            win_rate=win_rate,
            dominant_pattern=dominant,
            counts=dict(tag_counter),
        )
