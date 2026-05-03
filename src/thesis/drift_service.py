"""Drift service — detect price drift against thesis entry_price.

Owner: thesis segment.

Responsibility boundary:
    DriftService   → compute drift_pct, decide if threshold crossed + cooldown clear
    ReviewService  → execute AI review (called by scheduler, NOT by DriftService)
    Scheduler      → wire DriftService → ReviewService → Discord notify

DriftService is intentionally a pure detection layer. It never calls
ReviewService directly — that coupling lives in the scheduler (bot segment)
which owns orchestration decisions.

Flow (called by ThesisDriftScheduler every 15 min during market hours):
    1. Load all ACTIVE theses with entry_price for user.
    2. Fetch live quote per unique ticker (via injected QuoteService).
    3. Compute drift_pct = (current_price - entry_price) / entry_price * 100.
    4. If |drift_pct| >= threshold AND cooldown cleared → emit DriftSignal.
    5. Return list[DriftSignal] — scheduler drives the rest.

Cooldown check: thesis has a review within the last cooldown_hours → skip.
This prevents repeated AI calls when price stays outside threshold range.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.models import Thesis, ThesisReview, ThesisStatus

logger = get_logger(__name__)


@dataclass
class DriftSignal:
    """A single thesis that has drifted beyond the threshold."""

    thesis_id: int
    user_id: str
    ticker: str
    entry_price: float
    current_price: float
    drift_pct: float  # positive = up, negative = down

    @property
    def direction(self) -> str:
        return "▲" if self.drift_pct >= 0 else "▼"

    @property
    def summary(self) -> str:
        return (
            f"{self.ticker} drift {self.direction}{abs(self.drift_pct):.1f}% "
            f"(entry {self.entry_price:.0f} → now {self.current_price:.0f})"
        )


class DriftService:
    """Detect ACTIVE theses whose price has drifted beyond a threshold.

    Pure detection — no AI calls, no state mutation.
    """

    def __init__(
        self,
        session: AsyncSession,
        quote_service: object,  # QuoteService, avoid circular import
        threshold_pct: float = 5.0,
        cooldown_hours: float = 4.0,
    ) -> None:
        self._session = session
        self._quote_service = quote_service
        self._threshold_pct = threshold_pct
        self._cooldown_hours = cooldown_hours

    async def detect(self, user_id: str) -> list[DriftSignal]:
        """Return DriftSignals for theses that crossed the drift threshold
        and are not in cooldown.

        Args:
            user_id: User whose ACTIVE theses to check.

        Returns:
            List of DriftSignal — may be empty if nothing drifted or all in cooldown.
        """
        theses = await self._load_active_theses(user_id)
        if not theses:
            logger.debug("drift_service.detect.no_active_theses", user_id=user_id)
            return []

        # Deduplicate tickers for quote fetching
        tickers = sorted({t.ticker for t in theses if t.entry_price is not None})
        price_map: dict[str, float] = {}
        for ticker in tickers:
            try:
                quote = await self._quote_service.get_quote(ticker)  # type: ignore[union-attr]
                price_map[ticker] = quote.price
            except Exception as exc:
                logger.warning(
                    "drift_service.quote_fetch_failed",
                    ticker=ticker,
                    error=str(exc),
                )

        signals: list[DriftSignal] = []
        for thesis in theses:
            if thesis.entry_price is None:
                continue
            current_price = price_map.get(thesis.ticker)
            if current_price is None:
                continue

            drift_pct = (current_price - thesis.entry_price) / thesis.entry_price * 100

            if abs(drift_pct) < self._threshold_pct:
                logger.debug(
                    "drift_service.below_threshold",
                    thesis_id=thesis.id,
                    ticker=thesis.ticker,
                    drift_pct=round(drift_pct, 2),
                    threshold=self._threshold_pct,
                )
                continue

            if await self._in_cooldown(thesis.id):
                logger.debug(
                    "drift_service.cooldown_active",
                    thesis_id=thesis.id,
                    ticker=thesis.ticker,
                    drift_pct=round(drift_pct, 2),
                )
                continue

            signal = DriftSignal(
                thesis_id=thesis.id,
                user_id=user_id,
                ticker=thesis.ticker,
                entry_price=float(thesis.entry_price),
                current_price=current_price,
                drift_pct=round(drift_pct, 2),
            )
            signals.append(signal)
            logger.info(
                "drift_service.signal_emitted",
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                drift_pct=signal.drift_pct,
                direction=signal.direction,
            )

        return signals

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _load_active_theses(self, user_id: str) -> list[Thesis]:
        """Load ACTIVE theses with entry_price set, no eager-loads needed."""
        stmt = (
            select(Thesis)
            .where(Thesis.user_id == user_id)
            .where(Thesis.status == ThesisStatus.ACTIVE)
            .where(Thesis.entry_price.is_not(None))
            .order_by(Thesis.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _in_cooldown(self, thesis_id: int) -> bool:
        """Return True if thesis has a review within the last cooldown_hours."""
        cutoff = datetime.now(UTC) - timedelta(hours=self._cooldown_hours)
        stmt = (
            select(ThesisReview.id)
            .where(ThesisReview.thesis_id == thesis_id)
            .where(ThesisReview.reviewed_at >= cutoff)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None
