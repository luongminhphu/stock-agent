"""LessonService — query persisted AI lessons from DecisionLog.

Owner: thesis segment.

Responsibilities:
- Fetch recent key_lesson + pattern_detected for a user.
- Format them as plain text snippets ready for prompt injection.
- Aggregate SELL pattern tags into PatternCounter for behavioral feedback.

Non-responsibilities:
- Does not call AI.
- Does not write DecisionLog (that belongs to DecisionService.persist_lesson).
- Does not own briefing or pretrade prompt assembly.

Wave 9 additions:
  PatternCounter dataclass: lightweight value object that holds aggregated
  pattern counts, computes frequency %, and formats a compact warning block
  for AI context injection (used by ContextBuilder._fetch_replay_pattern).

  get_pattern_summary(user_id): queries DecisionLog SELL rows, groups by
  pattern_detected tag, returns PatternCounter when enough data exists.

  get_recent() alias: thin wrapper around get_recent_lessons() to match the
  (user_id, limit=N) call signature used by ContextBuilder._fetch_recent_lessons.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thesis.models import DecisionLog

_DEFAULT_LOOKBACK_DAYS = 90
_DEFAULT_MAX_LESSONS = 5
_DEFAULT_PATTERN_LOOKBACK_DAYS = 90
_MIN_OCCURRENCES = 2   # min pattern count to surface in warnings
_HIGH_FREQ_THRESHOLD = 0.30  # >= 30% → flagged as high-frequency bias

# Human-readable labels for known pattern tags.
# Unknown tags fall back to the raw tag string.
_PATTERN_LABELS: dict[str, str] = {
    "early_exit":           "thoát sớm trước khi thesis hoàn thành",
    "stop_loss_ignored":    "bỏ qua stop loss, giữ lịnh dù thesis đã sai",
    "premature_entry":      "vào lệnh trước khi catalyst xuất hiện",
    "breakout_chasing":     "mua đuổi theo breakout, giá đã chạy xa",
    "fomo_entry":           "mua theo tâm lý FOMO, thiếu phân tích",
    "overhold":             "giữ quá lâu sau khi tín hiệu đảo chiều",
    "thesis_drift":         "thesis đã thay đổi nhưng không cập nhật",
    "size_too_large":       "vào lệnh với size quá lớn so với rủi ro",
    "averaging_down_blind": "mua thêm khi lỗ mà không có luận cứ",
}


@dataclass(frozen=True)
class PatternEntry:
    """One aggregated pattern tag with occurrence count and frequency."""
    tag: str
    count: int
    total_sells: int

    @property
    def frequency(self) -> float:
        """Fraction of SELL trades that triggered this pattern."""
        if self.total_sells == 0:
            return 0.0
        return self.count / self.total_sells

    @property
    def label(self) -> str:
        return _PATTERN_LABELS.get(self.tag, self.tag.replace("_", " "))

    @property
    def is_high_freq(self) -> bool:
        return self.frequency >= _HIGH_FREQ_THRESHOLD


@dataclass
class PatternCounter:
    """Aggregated exit pattern data for a user over a lookback window.

    Used by ContextBuilder._fetch_replay_pattern() to inject behavioral
    warnings into AI agent context before verdict generation.

    Attributes:
        entries:       Sorted list of PatternEntry (most frequent first).
        total_sells:   Total SELL trades in the lookback window.
        lookback_days: Window used for this aggregation.
    """
    entries: list[PatternEntry] = field(default_factory=list)
    total_sells: int = 0
    lookback_days: int = _DEFAULT_PATTERN_LOOKBACK_DAYS

    def format_for_prompt(self) -> str:
        """Render a compact warning block for AI prompt injection.

        Example output::

            Exit patterns (last 90 ngày, 20 lệnh bán):
              - early_exit: 8 lần (40%) — thoát sớm trước khi thesis hoàn thành
              - stop_loss_ignored: 3 lần (15%) — bỏ qua stop loss, giữ lịnh dù thesis đã sai
            ⚠ Bias cảnh báo: xu hướng thoát sớm (40% lệnh) — cân nhắc giữ đến target.

        Returns empty string when no entries (caller skips injection).
        """
        if not self.entries:
            return ""

        header = (
            f"Exit patterns (last {self.lookback_days} ngày, "
            f"{self.total_sells} lệnh bán):"
        )
        lines = [header]
        for e in self.entries:
            pct = f"{e.frequency:.0%}"
            flag = " ⚠" if e.is_high_freq else ""
            lines.append(f"  - {e.tag}: {e.count} lần ({pct}){flag} — {e.label}")

        # Bias warning line: surface the highest-freq pattern as plain sentence
        high_freq = [e for e in self.entries if e.is_high_freq]
        if high_freq:
            top = high_freq[0]
            pct = f"{top.frequency:.0%}"
            lines.append(
                f"⚠ Bias cảnh báo: xu hướng {top.label} "
                f"({pct} lệnh) — agent nên nhắc nhở investor trước khi ra quyết định."
            )

        return "\n".join(lines)


@dataclass(frozen=True)
class LessonSnippet:
    """One persisted AI lesson, ready for prompt injection."""
    decision_id: int
    ticker: str
    decision_type: str
    outcome_verdict: str | None
    key_lesson: str
    pattern_detected: str | None
    decision_at: str  # ISO 8601 string


class LessonService:
    """Read-only view into persisted lessons from the Decision Replay loop.

    Two usage patterns:

    1. Low-level (get + format separately):
        snippets = await svc.get_recent_lessons(user_id)
        text = svc.format_for_prompt(snippets)

    2. High-level convenience (used by BriefingService + PreTradeService):
        text = await svc.build_lesson_context(user_id, ticker=ticker, limit=3)

    3. Pattern aggregation (used by ContextBuilder Wave 9):
        counter = await svc.get_pattern_summary(user_id)
        if counter:
            block = counter.format_for_prompt()
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    async def get_recent_lessons(
        self,
        user_id: str,
        *,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
        max_lessons: int = _DEFAULT_MAX_LESSONS,
        ticker: str | None = None,
    ) -> list[LessonSnippet]:
        """Return the most recent key_lesson entries for a user.

        Args:
            user_id:       Filter to this investor.
            lookback_days: Only consider decisions within this window.
            max_lessons:   Cap the number of returned snippets.
            ticker:        Optionally filter to a single ticker.

        Returns:
            List of LessonSnippet sorted newest-first, capped at max_lessons.
        """
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

        conditions = [
            DecisionLog.user_id == user_id,
            DecisionLog.key_lesson.isnot(None),
            DecisionLog.decision_at >= cutoff,
        ]
        if ticker:
            conditions.append(DecisionLog.ticker == ticker.upper())

        stmt = (
            select(DecisionLog)
            .where(and_(*conditions))
            .order_by(DecisionLog.decision_at.desc())
            .limit(max_lessons)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            LessonSnippet(
                decision_id=r.id,
                ticker=r.ticker,
                decision_type=r.decision_type,
                outcome_verdict=r.outcome_verdict,
                key_lesson=r.key_lesson,
                pattern_detected=r.pattern_detected,
                decision_at=r.decision_at.isoformat(),
            )
            for r in rows
        ]

    async def get_recent(
        self,
        user_id: str,
        *,
        limit: int = _DEFAULT_MAX_LESSONS,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    ) -> list[LessonSnippet]:
        """Alias for get_recent_lessons() with (limit=) kwarg signature.

        Used by ContextBuilder._fetch_recent_lessons().
        Delegates entirely to get_recent_lessons() — no separate logic.

        Args:
            user_id:       Investor to query.
            limit:         Max snippets to return (maps to max_lessons).
            lookback_days: How far back to look.

        Returns:
            List of LessonSnippet sorted newest-first.
        """
        return await self.get_recent_lessons(
            user_id,
            lookback_days=lookback_days,
            max_lessons=limit,
        )

    async def get_pattern_summary(
        self,
        user_id: str,
        *,
        lookback_days: int = _DEFAULT_PATTERN_LOOKBACK_DAYS,
        min_occurrences: int = _MIN_OCCURRENCES,
    ) -> PatternCounter | None:
        """Aggregate SELL pattern_detected tags into a PatternCounter.

        Queries DecisionLog for SELL decisions with pattern_detected set,
        counts occurrences per tag, and returns a PatternCounter when
        at least min_occurrences patterns are found.

        Returns None when there is insufficient data (caller skips injection).

        Algorithm:
          1. Fetch all SELL rows within lookback_days that have pattern_detected.
          2. Count total SELL trades in the same window (denominator).
          3. Group pattern tags with Counter, filter by min_occurrences.
          4. Build PatternEntry list sorted by count desc.
          5. Return PatternCounter or None.

        Args:
            user_id:          Investor to query.
            lookback_days:    Window for pattern aggregation (default 90 days).
            min_occurrences:  Minimum tag count to include in result (default 2).
                              Prevents surfacing spurious single-occurrence noise.

        Returns:
            PatternCounter with entries sorted by frequency desc,
            or None if no qualifying patterns found.
        """
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

        # --- Step 1: Fetch SELL rows with pattern_detected in window ---
        pattern_stmt = (
            select(DecisionLog.pattern_detected)
            .where(
                and_(
                    DecisionLog.user_id == user_id,
                    DecisionLog.decision_type == "SELL",
                    DecisionLog.pattern_detected.isnot(None),
                    DecisionLog.decision_at >= cutoff,
                )
            )
        )
        pattern_rows = (await self._session.execute(pattern_stmt)).scalars().all()

        if not pattern_rows:
            return None

        # --- Step 2: Count total SELL trades in window (denominator) ---
        total_stmt = (
            select(DecisionLog.id)
            .where(
                and_(
                    DecisionLog.user_id == user_id,
                    DecisionLog.decision_type == "SELL",
                    DecisionLog.decision_at >= cutoff,
                )
            )
        )
        total_sells = len(
            (await self._session.execute(total_stmt)).scalars().all()
        )

        # --- Step 3: Count per tag, filter by min_occurrences ---
        tag_counts: Counter[str] = Counter(pattern_rows)
        qualifying = {
            tag: count
            for tag, count in tag_counts.items()
            if count >= min_occurrences
        }

        if not qualifying:
            return None

        # --- Step 4: Build PatternEntry list, sort by count desc ---
        entries = sorted(
            [
                PatternEntry(tag=tag, count=count, total_sells=total_sells)
                for tag, count in qualifying.items()
            ],
            key=lambda e: e.count,
            reverse=True,
        )

        return PatternCounter(
            entries=entries,
            total_sells=total_sells,
            lookback_days=lookback_days,
        )

    async def build_lesson_context(
        self,
        user_id: str,
        *,
        ticker: str | None = None,
        limit: int = _DEFAULT_MAX_LESSONS,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    ) -> str:
        """Convenience wrapper: fetch + format in one call.

        Used by BriefingService and PreTradeService to inject past lessons
        into AI prompt context without needing to handle LessonSnippet objects.

        Args:
            user_id:       Investor to query.
            ticker:        Optional ticker filter (PreTrade uses this,
                           Briefing leaves it None for cross-ticker lessons).
            limit:         Max snippets to include (PreTrade: 3, Briefing: 5).
            lookback_days: How far back to look.

        Returns:
            Formatted string ready for prompt injection,
            or empty string if no lessons found.
        """
        snippets = await self.get_recent_lessons(
            user_id,
            lookback_days=lookback_days,
            max_lessons=limit,
            ticker=ticker,
        )
        return self.format_for_prompt(snippets)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_for_prompt(snippets: list[LessonSnippet]) -> str:
        """Render snippets as a compact multi-line string for prompt injection.

        Output example (injected into briefing / pretrade context)::

            === Past lessons from your decision history ===
            [2026-02-10] BUY VCB → CORRECT | Lesson: Breakout signal confirmed by
            volume surge was reliable when market breadth was positive.
            | Pattern: breakout_chasing
            [2026-01-03] BUY HPG → INCORRECT | Lesson: Entered before catalyst
            materialized; waited too short after earnings miss.
            | Pattern: premature_entry

        Returns empty string if snippets is empty (caller skips injection).
        """
        if not snippets:
            return ""

        lines = ["=== Past lessons from your decision history ==="]
        for s in snippets:
            verdict_part = f" → {s.outcome_verdict}" if s.outcome_verdict else ""
            date_str = s.decision_at[:10]  # YYYY-MM-DD
            pattern_part = f" | Pattern: {s.pattern_detected}" if s.pattern_detected else ""
            lines.append(
                f"[{date_str}] {s.decision_type} {s.ticker}{verdict_part} "
                f"| Lesson: {s.key_lesson}{pattern_part}"
            )
        return "\n".join(lines)
