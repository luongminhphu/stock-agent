"""Drift service — detect significant price moves against active theses.

Owner: thesis segment.

Responsibility boundary:
  DriftService  → detect which theses have drifted beyond threshold
                  returns list[DriftSignal], does NOT trigger reviews
  Caller        → decides whether to call ReviewService based on signals

Design decisions:
  - Only ACTIVE theses with an entry_price are evaluated.
  - drift_pct is computed as (current_price - entry_price) / entry_price * 100.
  - A cooldown (default 4h) prevents re-triggering on the same thesis
    while the market stays volatile. Cooldown state is in-memory only
    (resets on bot restart) — acceptable for a single-process deployment.
  - Threshold is configurable via settings.thesis_drift_threshold_pct (default 5.0).

This service never calls ReviewService directly — that is the scheduler's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger
from src.thesis.models import Thesis, ThesisStatus
from src.thesis.repository import ThesisRepository

logger = get_logger(__name__)

_DEFAULT_THRESHOLD_PCT = 5.0   # ±5% move triggers a drift review
_DEFAULT_COOLDOWN_HOURS = 4    # minimum hours between drift reviews per thesis


@dataclass
class DriftSignal:
    """Represents a single thesis that has drifted beyond the threshold."""

    thesis_id: int
    ticker: str
    user_id: str
    entry_price: float
    current_price: float
    drift_pct: float           # positive = price up, negative = price down

    @property
    def direction(self) -> str:
        return "up" if self.drift_pct >= 0 else "down"

    @property
    def summary(self) -> str:
        icon = "📈" if self.drift_pct >= 0 else "📉"
        return (
            f"{icon} **{self.ticker}** drift {self.drift_pct:+.1f}% "
            f"(entry {self.entry_price:.0f} → now {self.current_price:.0f})"
        )


class DriftService:
    """Detect active theses whose price has moved beyond the drift threshold.

    Maintains an in-memory cooldown registry so the same thesis is not
    flagged repeatedly within a short window.

    Args:
        session:           AsyncSession (per-call).
        quote_service:     QuoteService adapter (market segment).
        threshold_pct:     Absolute drift % to trigger a signal (default 5.0).
        cooldown_hours:    Hours to suppress re-trigger per thesis (default 4).
    """

    # Class-level cooldown registry — shared across all DriftService instances
    # within the same process. Keys are thesis_id, values are last-triggered UTC.
    _cooldown_registry: dict[int, datetime] = {}

    def __init__(
        self,
        session: AsyncSession,
        quote_service: object,
        threshold_pct: float = _DEFAULT_THRESHOLD_PCT,
        cooldown_hours: int = _DEFAULT_COOLDOWN_HOURS,
    ) -> None:
        self._repo = ThesisRepository(session)
        self._quote_service = quote_service
        self._threshold_pct = threshold_pct
        self._cooldown_hours = cooldown_hours

    async def detect(self, user_id: str) -> list[DriftSignal]:
        """Scan all ACTIVE theses for the user and return drift signals.

        Only theses with a set entry_price are evaluated.
        Theses on cooldown are silently skipped.

        Args:
            user_id: Owner of the theses to scan.

        Returns:
            List of DriftSignal — may be empty if no drift detected.
        """
        theses = await self._repo.list_by_user(user_id, status=ThesisStatus.ACTIVE)
        eligible = [t for t in theses if t.entry_price is not None]

        if not eligible:
            logger.info("drift_service.detect.no_eligible", user_id=user_id)
            return []

        # Deduplicate tickers to minimise quote API calls
        tickers = sorted({t.ticker for t in eligible})
        price_map: dict[str, float] = {}
        for ticker in tickers:
            try:
                quote = await self._quote_service.get_quote(ticker)  # type: ignore[union-attr]
                price_map[ticker] = quote.price
            except Exception as exc:
                logger.warning(
                    "drift_service.detect.quote_error",
                    ticker=ticker,
                    error=str(exc),
                )

        signals: list[DriftSignal] = []
        now = datetime.now(UTC)

        for thesis in eligible:
            current_price = price_map.get(thesis.ticker)
            if current_price is None:
                continue

            drift_pct = self._compute_drift_pct(thesis, current_price)
            if abs(drift_pct) < self._threshold_pct:
                continue

            if self._is_on_cooldown(thesis.id, now):
                logger.info(
                    "drift_service.detect.cooldown_skip",
                    thesis_id=thesis.id,
                    ticker=thesis.ticker,
                    drift_pct=round(drift_pct, 2),
                )
                continue

            signal = DriftSignal(
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                user_id=thesis.user_id,
                entry_price=float(thesis.entry_price),  # type: ignore[arg-type]
                current_price=current_price,
                drift_pct=round(drift_pct, 2),
            )
            signals.append(signal)
            self._register_cooldown(thesis.id, now)
            logger.info(
                "drift_service.detect.signal",
                thesis_id=thesis.id,
                ticker=thesis.ticker,
                drift_pct=signal.drift_pct,
                direction=signal.direction,
            )

        logger.info(
            "drift_service.detect.done",
            user_id=user_id,
            eligible=len(eligible),
            signals=len(signals),
        )
        return signals

    def mark_reviewed(self, thesis_id: int) -> None:
        """Reset cooldown clock after a drift-triggered review completes.

        Called by the scheduler after ReviewService.review_thesis() succeeds.
        Ensures the next genuine drift will fire again after cooldown_hours.
        """
        DriftService._cooldown_registry[thesis_id] = datetime.now(UTC)
        logger.debug("drift_service.cooldown_reset", thesis_id=thesis_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_drift_pct(thesis: Thesis, current_price: float) -> float:
        """Return signed drift % relative to entry_price."""
        entry = float(thesis.entry_price)  # type: ignore[arg-type]
        if entry == 0:
            return 0.0
        return (current_price - entry) / entry * 100

    def _is_on_cooldown(self, thesis_id: int, now: datetime) -> bool:
        last = DriftService._cooldown_registry.get(thesis_id)
        if last is None:
            return False
        return (now - last) < timedelta(hours=self._cooldown_hours)

    def _register_cooldown(self, thesis_id: int, now: datetime) -> None:
        DriftService._cooldown_registry[thesis_id] = now
