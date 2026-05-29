"""BehavioralDNAService — aggregate DecisionLog history into investor behavioral profile.

Owner: thesis segment.

Responsibilities:
  - Query DecisionLog for a user over a configurable lookback window.
  - Aggregate into a BehavioralDNA dataclass covering:
      * Holding duration patterns (winners vs losers)
      * Early-exit winner rate / late-exit loser rate
      * Best/worst decision day-of-week
      * Top recurring behavioral patterns (from pattern_detected)
      * Win rate by decision type (BUY / SELL)
  - Render the profile as prompt-injectable text (format_for_prompt)
    and as a human-readable summary (format_for_display).

Non-responsibilities:
  - Does NOT call AI — pure SQL aggregation.
  - Does NOT write any data.
  - Does NOT own briefing or pretrade prompt assembly —
    those segments import this service and call format_for_prompt().

Usage (BriefingService / PreTradeService)::

    svc = BehavioralDNAService(session)
    dna = await svc.analyze(user_id)
    prompt_block = dna.format_for_prompt()   # inject into AI context
    display_text = dna.format_for_display()  # send to Discord / API

Data contract:
  - All numeric fields are None when insufficient data (<3 evaluated trades).
  - top_patterns is empty list when no pattern_detected data exists.
  - Never raises — returns a zero-data BehavioralDNA on any query failure.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.models import DecisionLog, OutcomeVerdict

logger = get_logger(__name__)

_DEFAULT_LOOKBACK_DAYS = 365
_MIN_SAMPLE_SIZE = 3          # minimum evaluated trades for a metric to be meaningful
_EARLY_EXIT_THRESHOLD = 0.5   # sold when <50% of target upside remaining
_LATE_EXIT_DAYS = 30          # still holding N days after stop loss price breached


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class BehavioralDNA:
    """Aggregated behavioral profile of an investor.

    All float/int metrics are None when sample size is below _MIN_SAMPLE_SIZE.
    Consumers should check for None before displaying.
    """

    # Holding duration
    avg_hold_days_winners: float | None = None
    avg_hold_days_losers: float | None = None

    # Exit discipline
    early_exit_winner_rate: float | None = None   # 0.0–1.0
    late_exit_loser_rate: float | None = None      # 0.0–1.0

    # Timing patterns
    best_decision_day: str | None = None           # e.g. "Wednesday"
    worst_decision_day: str | None = None          # e.g. "Monday"
    day_win_rates: dict[str, float] = field(default_factory=dict)

    # Recurring behavioral patterns
    top_patterns: list[tuple[str, int]] = field(default_factory=list)

    # Win rates
    win_rate_buy: float | None = None
    win_rate_sell: float | None = None
    win_rate_overall: float | None = None

    # Meta
    total_evaluated: int = 0
    total_decisions: int = 0
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS
    generated_at: str = ""

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def format_for_prompt(self) -> str:
        """Render DNA as structured text for AI prompt injection.

        Designed for BriefingService and PreTradeService context blocks.
        Returns empty string when no meaningful data is available.

        Output example::

            === Behavioral DNA (365-day profile, 18 evaluated trades) ===
            Hold duration  : Winners avg 12.3 days | Losers avg 41.7 days → giữ loser lâu hơn winner 3.4x
            Exit discipline: Bán winner sớm 67% lần | Giữ loser quá lâu 44% lần
            Best day       : Wednesday (win rate 75%) | Worst: Monday (win rate 25%)
            Top patterns   : premature_entry (×5) · stop_loss_avoidance (×3) · thesis_drift (×2)
            Win rate       : Overall 44% | BUY 50% | SELL 33%
        """
        if self.total_evaluated < _MIN_SAMPLE_SIZE:
            return ""

        lines = [
            f"=== Behavioral DNA ({self.lookback_days}-day profile, "
            f"{self.total_evaluated} evaluated trades) ==="
        ]

        # Hold duration
        if self.avg_hold_days_winners is not None and self.avg_hold_days_losers is not None:
            ratio = self.avg_hold_days_losers / max(self.avg_hold_days_winners, 0.1)
            direction = "giữ loser lâu hơn winner" if ratio > 1 else "cắt loser nhanh hơn winner"
            lines.append(
                f"Hold duration  : Winners avg {self.avg_hold_days_winners:.1f} ngày"
                f" | Losers avg {self.avg_hold_days_losers:.1f} ngày"
                f" → {direction} {abs(ratio):.1f}x"
            )
        elif self.avg_hold_days_winners is not None:
            lines.append(f"Hold duration  : Winners avg {self.avg_hold_days_winners:.1f} ngày")
        elif self.avg_hold_days_losers is not None:
            lines.append(f"Hold duration  : Losers avg {self.avg_hold_days_losers:.1f} ngày")

        # Exit discipline
        exit_parts = []
        if self.early_exit_winner_rate is not None:
            pct = round(self.early_exit_winner_rate * 100)
            exit_parts.append(f"Bán winner sớm {pct}% lần")
        if self.late_exit_loser_rate is not None:
            pct = round(self.late_exit_loser_rate * 100)
            exit_parts.append(f"Giữ loser quá lâu {pct}% lần")
        if exit_parts:
            lines.append(f"Exit discipline: {' | '.join(exit_parts)}")

        # Day-of-week
        if self.best_decision_day and self.worst_decision_day:
            best_rate = round(self.day_win_rates.get(self.best_decision_day, 0) * 100)
            worst_rate = round(self.day_win_rates.get(self.worst_decision_day, 0) * 100)
            lines.append(
                f"Best day       : {self.best_decision_day} (win rate {best_rate}%)"
                f" | Worst: {self.worst_decision_day} (win rate {worst_rate}%)"
            )

        # Top patterns
        if self.top_patterns:
            pattern_str = " · ".join(
                f"{p} (×{c})" for p, c in self.top_patterns[:5]
            )
            lines.append(f"Top patterns   : {pattern_str}")

        # Win rates
        wr_parts = []
        if self.win_rate_overall is not None:
            wr_parts.append(f"Overall {round(self.win_rate_overall * 100)}%")
        if self.win_rate_buy is not None:
            wr_parts.append(f"BUY {round(self.win_rate_buy * 100)}%")
        if self.win_rate_sell is not None:
            wr_parts.append(f"SELL {round(self.win_rate_sell * 100)}%")
        if wr_parts:
            lines.append(f"Win rate       : {' | '.join(wr_parts)}")

        return "\n".join(lines)

    def format_for_display(self) -> str:
        """Render DNA as a human-readable summary for Discord or API response.

        More narrative than format_for_prompt — uses emoji and plain Vietnamese.
        Returns a fallback message when data is insufficient.
        """
        if self.total_evaluated < _MIN_SAMPLE_SIZE:
            return (
                f"📊 Chưa đủ dữ liệu để phân tích Behavioral DNA "
                f"(cần tối thiểu {_MIN_SAMPLE_SIZE} quyết định đã có kết quả).\n"
                f"Hiện có {self.total_evaluated} quyết định đã evaluate trong "
                f"{self.lookback_days} ngày qua."
            )

        lines = [
            f"🧬 **Behavioral DNA** — {self.lookback_days} ngày qua · "
            f"{self.total_evaluated}/{self.total_decisions} quyết định có kết quả\n"
        ]

        if self.avg_hold_days_winners is not None and self.avg_hold_days_losers is not None:
            if self.avg_hold_days_losers > self.avg_hold_days_winners * 1.5:
                lines.append(
                    f"⚠️ **Giữ loser lâu:** Losers trung bình {self.avg_hold_days_losers:.0f} ngày"
                    f" vs Winners {self.avg_hold_days_winners:.0f} ngày"
                )
            else:
                lines.append(
                    f"✅ **Hold balance:** Winners {self.avg_hold_days_winners:.0f} ngày"
                    f" · Losers {self.avg_hold_days_losers:.0f} ngày"
                )

        if self.early_exit_winner_rate is not None and self.early_exit_winner_rate > 0.5:
            lines.append(
                f"⚠️ **Bán winner sớm:** {round(self.early_exit_winner_rate * 100)}% lần "
                f"bạn thoát khi còn <50% target"
            )

        if self.late_exit_loser_rate is not None and self.late_exit_loser_rate > 0.4:
            lines.append(
                f"⚠️ **Giữ loser quá lâu:** {round(self.late_exit_loser_rate * 100)}% lần "
                f"vẫn giữ >{_LATE_EXIT_DAYS} ngày sau khi stop bị phá"
            )

        if self.best_decision_day:
            best_rate = round(self.day_win_rates.get(self.best_decision_day, 0) * 100)
            lines.append(f"📅 **Best day:** {self.best_decision_day} (win rate {best_rate}%)")

        if self.top_patterns:
            top = self.top_patterns[0]
            lines.append(f"🔁 **Pattern nổi bật:** `{top[0]}` xuất hiện {top[1]} lần")

        if self.win_rate_overall is not None:
            pct = round(self.win_rate_overall * 100)
            emoji = "✅" if pct >= 50 else "📉"
            lines.append(f"{emoji} **Win rate overall:** {pct}%")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class BehavioralDNAService:
    """Aggregate DecisionLog history into a BehavioralDNA profile.

    Usage::

        svc = BehavioralDNAService(session)
        dna = await svc.analyze(user_id)                  # full profile
        dna = await svc.analyze(user_id, lookback_days=90) # shorter window

        # Inject into AI context
        prompt_block = dna.format_for_prompt()

        # Send to Discord
        display = dna.format_for_display()
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def analyze(
        self,
        user_id: str,
        *,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    ) -> BehavioralDNA:
        """Build BehavioralDNA from DecisionLog history.

        Args:
            user_id:       Investor to profile.
            lookback_days: How far back to look (default 365 days).

        Returns:
            BehavioralDNA — all fields nullable, never raises.
        """
        try:
            return await self._compute(user_id, lookback_days)
        except Exception as exc:
            logger.warning(
                "behavioral_dna.analyze_failed",
                user_id=user_id,
                lookback_days=lookback_days,
                error=str(exc),
            )
            return BehavioralDNA(
                lookback_days=lookback_days,
                generated_at=datetime.now(UTC).isoformat(),
            )

    # ------------------------------------------------------------------
    # Internal computation
    # ------------------------------------------------------------------

    async def _compute(self, user_id: str, lookback_days: int) -> BehavioralDNA:
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

        stmt = (
            select(DecisionLog)
            .where(
                and_(
                    DecisionLog.user_id == user_id,
                    DecisionLog.decision_at >= cutoff,
                )
            )
            .order_by(DecisionLog.decision_at.asc())
        )
        rows: list[DecisionLog] = list(
            (await self._session.execute(stmt)).scalars().all()
        )

        total_decisions = len(rows)
        evaluated = [r for r in rows if r.outcome_verdict is not None]
        total_evaluated = len(evaluated)

        dna = BehavioralDNA(
            total_decisions=total_decisions,
            total_evaluated=total_evaluated,
            lookback_days=lookback_days,
            generated_at=datetime.now(UTC).isoformat(),
        )

        if total_evaluated < _MIN_SAMPLE_SIZE:
            return dna

        winners = [
            r for r in evaluated
            if r.outcome_verdict == OutcomeVerdict.CORRECT
        ]
        losers = [
            r for r in evaluated
            if r.outcome_verdict == OutcomeVerdict.INCORRECT
        ]

        # -- Hold duration -------------------------------------------------
        dna.avg_hold_days_winners = _avg_hold_days(winners)
        dna.avg_hold_days_losers = _avg_hold_days(losers)

        # -- Exit discipline -----------------------------------------------
        dna.early_exit_winner_rate = _early_exit_rate(winners)
        dna.late_exit_loser_rate = _late_exit_rate(losers)

        # -- Day-of-week win rates -----------------------------------------
        day_win_rates, best_day, worst_day = _day_of_week_stats(evaluated)
        dna.day_win_rates = day_win_rates
        dna.best_decision_day = best_day
        dna.worst_decision_day = worst_day

        # -- Top patterns --------------------------------------------------
        patterns = [r.pattern_detected for r in rows if r.pattern_detected]
        if patterns:
            counter = Counter(patterns)
            dna.top_patterns = counter.most_common(5)

        # -- Win rates -----------------------------------------------------
        dna.win_rate_overall = len(winners) / total_evaluated if total_evaluated else None

        buy_rows = [r for r in evaluated if str(r.decision_type).upper() == "BUY"]
        sell_rows = [r for r in evaluated if str(r.decision_type).upper() == "SELL"]

        if len(buy_rows) >= _MIN_SAMPLE_SIZE:
            buy_wins = sum(1 for r in buy_rows if r.outcome_verdict == OutcomeVerdict.CORRECT)
            dna.win_rate_buy = buy_wins / len(buy_rows)

        if len(sell_rows) >= _MIN_SAMPLE_SIZE:
            sell_wins = sum(1 for r in sell_rows if r.outcome_verdict == OutcomeVerdict.CORRECT)
            dna.win_rate_sell = sell_wins / len(sell_rows)

        return dna


# ---------------------------------------------------------------------------
# Pure aggregation helpers
# ---------------------------------------------------------------------------


def _avg_hold_days(logs: list[DecisionLog]) -> float | None:
    """Average hold duration in days for a list of evaluated DecisionLogs.

    Uses outcome_evaluated_at - decision_at as proxy for hold duration.
    Returns None when fewer than _MIN_SAMPLE_SIZE entries have both timestamps.
    """
    durations = []
    for r in logs:
        if r.outcome_evaluated_at and r.decision_at:
            delta = (r.outcome_evaluated_at - r.decision_at).total_seconds() / 86400
            if delta >= 0:
                durations.append(delta)
    if len(durations) < _MIN_SAMPLE_SIZE:
        return None
    return round(sum(durations) / len(durations), 1)


def _early_exit_rate(winners: list[DecisionLog]) -> float | None:
    """Fraction of winning trades where hold duration < review_horizon_days * threshold.

    Proxy for "sold winner before reaching full target":
    if actual hold < 50% of the review horizon the investor set for themselves,
    we flag it as an early exit.

    Returns None when sample is too small.
    """
    if len(winners) < _MIN_SAMPLE_SIZE:
        return None
    early = 0
    counted = 0
    for r in winners:
        if r.outcome_evaluated_at and r.decision_at and r.review_horizon_days:
            hold = (r.outcome_evaluated_at - r.decision_at).total_seconds() / 86400
            threshold = r.review_horizon_days * _EARLY_EXIT_THRESHOLD
            if hold < threshold:
                early += 1
            counted += 1
    if counted < _MIN_SAMPLE_SIZE:
        return None
    return round(early / counted, 3)


def _late_exit_rate(losers: list[DecisionLog]) -> float | None:
    """Fraction of losing trades held longer than _LATE_EXIT_DAYS after decision.

    Proxy for "didn't cut loss on time".
    Returns None when sample is too small.
    """
    if len(losers) < _MIN_SAMPLE_SIZE:
        return None
    late = 0
    counted = 0
    for r in losers:
        if r.outcome_evaluated_at and r.decision_at:
            hold = (r.outcome_evaluated_at - r.decision_at).total_seconds() / 86400
            if hold > _LATE_EXIT_DAYS:
                late += 1
            counted += 1
    if counted < _MIN_SAMPLE_SIZE:
        return None
    return round(late / counted, 3)


_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _day_of_week_stats(
    evaluated: list[DecisionLog],
) -> tuple[dict[str, float], str | None, str | None]:
    """Compute per-day win rates and identify best/worst decision days.

    Returns:
        (day_win_rates, best_day, worst_day)
        day_win_rates maps day name → win rate (0.0–1.0)
        best_day / worst_day are None when no day has >= _MIN_SAMPLE_SIZE trades.
    """
    day_wins: dict[str, int] = {}
    day_total: dict[str, int] = {}

    for r in evaluated:
        if not r.decision_at:
            continue
        day_name = _DAYS[r.decision_at.weekday()]
        day_total[day_name] = day_total.get(day_name, 0) + 1
        if r.outcome_verdict == OutcomeVerdict.CORRECT:
            day_wins[day_name] = day_wins.get(day_name, 0) + 1

    # Only consider days with at least _MIN_SAMPLE_SIZE trades
    qualified = {
        day: day_wins.get(day, 0) / total
        for day, total in day_total.items()
        if total >= _MIN_SAMPLE_SIZE
    }

    if not qualified:
        return {}, None, None

    best = max(qualified, key=lambda d: qualified[d])
    worst = min(qualified, key=lambda d: qualified[d])

    # Avoid reporting same day as both best and worst
    if best == worst:
        return qualified, best, None

    return qualified, best, worst
