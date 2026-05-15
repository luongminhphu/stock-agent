"""
Conviction Drift Detector — detect sustained conviction decay across reviews.

Owner: thesis segment.

Distinct from DriftService (price-based):
    DriftService                → price vs entry_price
    ConvictionDriftDetector     → conviction trend across ThesisReview.confidence

Detection patterns (evaluated in priority order):
    1. SCORE_FLOOR       — current confidence < floor_threshold (absolute floor)
    2. SUSTAINED_DECLINE — last N reviews strictly declining (monotone)
    3. CUMULATIVE_DROP   — drop from peak within window >= cumulative_drop_pct

Cooldown:
    If a ThesisReview exists within cooldown_hours → skip (same pattern as DriftService).
    Prevents repeated signals when conviction stagnates at a low level.

Flow (called by scheduler every cycle during market hours):
    1. Load ACTIVE theses for user.
    2. For each thesis: check cooldown → load confidence sequence → evaluate patterns.
    3. Return list[ConvictionDriftSignal] — scheduler/bot drives action (AI review, notify).

No AI calls. No state mutation. Pure detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.models import Thesis, ThesisReview, ThesisStatus

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------


class DriftPattern(StrEnum):
    SUSTAINED_DECLINE = "SUSTAINED_DECLINE"  # N consecutive reviews declining
    CUMULATIVE_DROP = "CUMULATIVE_DROP"       # total drop from peak >= threshold
    SCORE_FLOOR = "SCORE_FLOOR"              # current confidence < absolute floor


@dataclass
class ConvictionDriftSignal:
    """Emitted when a thesis conviction pattern triggers detection."""

    thesis_id: int
    user_id: str
    ticker: str
    pattern: DriftPattern
    current_score: float
    reference_score: float    # peak score (CUMULATIVE_DROP) or first-of-sequence
    drop_pct: float           # (reference - current) / reference * 100
    review_count: int         # number of reviews used in detection window
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def severity(self) -> str:
        if self.drop_pct >= 40 or self.current_score < 0.25:
            return "CRITICAL"
        if self.drop_pct >= 25 or self.current_score < 0.35:
            return "HIGH"
        return "MEDIUM"

    @property
    def summary(self) -> str:
        return (
            f"{self.ticker} conviction {self.pattern.value}: "
            f"{self.reference_score:.2f} → {self.current_score:.2f} "
            f"(-{self.drop_pct:.1f}%) [{self.severity}]"
        )


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class ConvictionDriftDetector:
    """Detect conviction score decay patterns across thesis reviews.

    Pure detection — no AI calls, no state mutation.

    Args:
        session:             AsyncSession for DB queries.
        window_days:         Look-back window for reviews (default 30 days).
        sustained_n:         Min consecutive declining reviews to trigger
                             SUSTAINED_DECLINE (default 3).
        cumulative_drop_pct: Drop from peak (%) to trigger CUMULATIVE_DROP
                             (default 25.0).
        floor_threshold:     Absolute conviction floor to trigger SCORE_FLOOR
                             (default 0.35).
        cooldown_hours:      Skip thesis if reviewed within this window
                             (default 4.0, matches DriftService default).
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        window_days: int = 30,
        sustained_n: int = 3,
        cumulative_drop_pct: float = 25.0,
        floor_threshold: float = 0.35,
        cooldown_hours: float = 4.0,
    ) -> None:
        self._session = session
        self._window_days = window_days
        self._sustained_n = sustained_n
        self._cumulative_drop_pct = cumulative_drop_pct
        self._floor_threshold = floor_threshold
        self._cooldown_hours = cooldown_hours

    async def detect_all(self, user_id: str) -> list[ConvictionDriftSignal]:
        """Scan all ACTIVE theses for user, return conviction drift signals."""
        theses = await self._load_active_theses(user_id)
        signals: list[ConvictionDriftSignal] = []
        for thesis in theses:
            sig = await self._detect_one(thesis)
            if sig:
                signals.append(sig)
                logger.info(
                    "conviction_drift.signal_emitted",
                    thesis_id=thesis.id,
                    ticker=thesis.ticker,
                    pattern=sig.pattern,
                    severity=sig.severity,
                    current_score=sig.current_score,
                    drop_pct=sig.drop_pct,
                )
        return signals

    # ------------------------------------------------------------------
    # Core detection
    # ------------------------------------------------------------------

    async def _detect_one(self, thesis: Thesis) -> ConvictionDriftSignal | None:
        """Evaluate one thesis — return first matching signal, or None."""
        if await self._in_cooldown(thesis.id):
            logger.debug(
                "conviction_drift.cooldown_active",
                thesis_id=thesis.id,
                ticker=thesis.ticker,
            )
            return None

        scores = await self._load_recent_scores(thesis.id)
        if len(scores) < 2:
            logger.debug(
                "conviction_drift.insufficient_reviews",
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                review_count=len(scores),
            )
            return None

        current = scores[-1]

        # Pattern 1: SCORE_FLOOR (cheapest check first)
        if current < self._floor_threshold:
            ref = max(scores)  # show full drop from best point
            return self._make_signal(thesis, DriftPattern.SCORE_FLOOR, current, ref, scores)

        # Pattern 2: SUSTAINED_DECLINE (last N strictly monotone decreasing)
        if len(scores) >= self._sustained_n:
            tail = scores[-self._sustained_n :]
            if all(tail[i] > tail[i + 1] for i in range(len(tail) - 1)):
                ref = tail[0]
                return self._make_signal(
                    thesis, DriftPattern.SUSTAINED_DECLINE, current, ref, scores
                )

        # Pattern 3: CUMULATIVE_DROP from peak within window
        peak = max(scores)
        if peak > 0:
            drop_pct = (peak - current) / peak * 100
            if drop_pct >= self._cumulative_drop_pct:
                return self._make_signal(
                    thesis, DriftPattern.CUMULATIVE_DROP, current, peak, scores
                )

        return None

    def _make_signal(
        self,
        thesis: Thesis,
        pattern: DriftPattern,
        current: float,
        reference: float,
        scores: list[float],
    ) -> ConvictionDriftSignal:
        drop_pct = (reference - current) / reference * 100 if reference > 0 else 0.0
        return ConvictionDriftSignal(
            thesis_id=thesis.id,
            user_id=thesis.user_id,
            ticker=thesis.ticker,
            pattern=pattern,
            current_score=round(current, 3),
            reference_score=round(reference, 3),
            drop_pct=round(drop_pct, 1),
            review_count=len(scores),
        )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _load_active_theses(self, user_id: str) -> list[Thesis]:
        stmt = (
            select(Thesis)
            .where(Thesis.user_id == user_id)
            .where(Thesis.status == ThesisStatus.ACTIVE)
            .order_by(Thesis.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _load_recent_scores(self, thesis_id: int) -> list[float]:
        """Load ThesisReview.confidence sequence (oldest→newest) within window."""
        cutoff = datetime.now(UTC) - timedelta(days=self._window_days)
        stmt = (
            select(ThesisReview.confidence)
            .where(ThesisReview.thesis_id == thesis_id)
            .where(ThesisReview.reviewed_at >= cutoff)
            .order_by(ThesisReview.reviewed_at.asc())
        )
        result = await self._session.execute(stmt)
        return [float(row) for row in result.scalars().all()]

    async def _in_cooldown(self, thesis_id: int) -> bool:
        """Return True if thesis has a review within the last cooldown_hours.

        Mirrors DriftService._in_cooldown — prevents repeated signals when
        conviction stagnates at a low level after a recent review cycle.
        """
        cutoff = datetime.now(UTC) - timedelta(hours=self._cooldown_hours)
        stmt = (
            select(ThesisReview.id)
            .where(ThesisReview.thesis_id == thesis_id)
            .where(ThesisReview.reviewed_at >= cutoff)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None
