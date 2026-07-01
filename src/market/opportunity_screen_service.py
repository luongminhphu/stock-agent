"""Opportunity Screen Service — market segment.

Owner: market segment.
Consumers: run_opportunity_screen_job() (called by scheduler + bot command).

Responsibilities:
- Scan ALL tickers in the market registry against live quotes.
- Apply 4 lightweight screens (momentum, breakout, volume_surge,
  reversal_watch) to produce a ranked list of ScreenCandidates.
- Emit OpportunityScreenCompletedEvent via EventBus after each scan.

Non-responsibilities:
- Does NOT call AI — that's ai.opportunity_screen_subscriber's job.
- Does NOT persist candidates — they are ephemeral scan results.
- Does NOT filter by user watchlist — this is a market-wide screen.
  User personalisation happens downstream in the AI subscriber.

Screening criteria (HOSE/HNX defaults, tunable via constructor):
  MOMENTUM       — change_pct >= momentum_min_pct (default 2.0%)
  BREAKOUT       — change_pct >= breakout_min_pct AND volume_ratio >= 1.5x
  VOLUME_SURGE   — volume_ratio >= volume_surge_ratio (default 2.0x)
                   regardless of price direction (can be down-day accumulation)
  REVERSAL_WATCH — change_pct <= reversal_max_pct (default -3.0%) with
                   volume_ratio >= reversal_volume_ratio (default 1.3x)
                   — oversold bounce candidates

Scoring:
  breakout_score  = clamp(change_pct / breakout_cap, 0, 1)  * volume bonus
  momentum_score  = clamp(change_pct / momentum_cap, 0, 1)
  composite_score = 0.6 * breakout_score + 0.4 * momentum_score

Output is sorted by composite_score DESC, capped at top_n (default 10).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.market.registry_types import Exchange
from src.platform.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# ── tuneable constants ───────────────────────────────────────────────────────
DEFAULT_TOP_N = 10
DEFAULT_MOMENTUM_MIN_PCT = 2.0
DEFAULT_BREAKOUT_MIN_PCT = 4.0
DEFAULT_BREAKOUT_VOLUME_RATIO = 1.5
DEFAULT_VOLUME_SURGE_RATIO = 2.0
DEFAULT_REVERSAL_MAX_PCT = -3.0
DEFAULT_REVERSAL_VOLUME_RATIO = 1.3
BREAKOUT_SCORE_CAP_PCT = 10.0   # 10% move = score 1.0
MOMENTUM_SCORE_CAP_PCT = 6.0    # 6% move = score 1.0


@dataclass
class ScreenCandidate:
    """A ticker that passed at least one screen criterion.

    Fields:
        ticker          : e.g. "VCB"
        current_price   : live price at scan time
        change_pct      : % change vs previous close
        volume_ratio    : current volume / 20d avg volume
        breakout_score  : 0.0 – 1.0
        momentum_score  : 0.0 – 1.0
        composite_score : weighted combo (0.6 breakout + 0.4 momentum)
        screen_criteria : list of matched screen names, e.g. ["BREAKOUT", "MOMENTUM"]
        detected_at     : UTC timestamp of scan
    """

    ticker: str
    current_price: float
    change_pct: float
    volume_ratio: float
    breakout_score: float
    momentum_score: float
    composite_score: float
    screen_criteria: list[str] = field(default_factory=list)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def format_for_prompt(self) -> str:
        """Compact one-liner for AI prompt injection.

        Example:
            VCB  +5.2%  vol=2.1x  score=0.78  [BREAKOUT, MOMENTUM]
        """
        criteria_str = ", ".join(self.screen_criteria) if self.screen_criteria else "-"
        return (
            f"{self.ticker:<6} {self.change_pct:+.1f}%  "
            f"vol={self.volume_ratio:.1f}x  "
            f"score={self.composite_score:.2f}  "
            f"[{criteria_str}]"
        )


@dataclass
class ScreenResult:
    """Full result of one opportunity scan run."""

    scanned_at: datetime
    candidates: list[ScreenCandidate]
    total_tickers_scanned: int
    duration_seconds: float
    trading_date: str  # YYYY-MM-DD

    @property
    def top_ticker(self) -> str:
        return self.candidates[0].ticker if self.candidates else ""

    @property
    def screen_criteria_summary(self) -> str:
        """Comma-joined unique criteria across all candidates."""
        all_criteria: set[str] = set()
        for c in self.candidates:
            all_criteria.update(c.screen_criteria)
        return ", ".join(sorted(all_criteria)) if all_criteria else "none"


class OpportunityScreenService:
    """Scan market registry tickers against live quotes and rank opportunities.

    By default only HOSE tickers are scanned. Pass exchange=None to scan
    the full registry (all three exchanges).

    Usage::

        svc = OpportunityScreenService(quote_service)              # HOSE only
        svc = OpportunityScreenService(quote_service, exchange=None)  # all
        result = await svc.run()
        for candidate in result.candidates:
            print(candidate.format_for_prompt())
    """

    def __init__(
        self,
        quote_service: object,
        top_n: int = DEFAULT_TOP_N,
        momentum_min_pct: float = DEFAULT_MOMENTUM_MIN_PCT,
        breakout_min_pct: float = DEFAULT_BREAKOUT_MIN_PCT,
        breakout_volume_ratio: float = DEFAULT_BREAKOUT_VOLUME_RATIO,
        volume_surge_ratio: float = DEFAULT_VOLUME_SURGE_RATIO,
        reversal_max_pct: float = DEFAULT_REVERSAL_MAX_PCT,
        reversal_volume_ratio: float = DEFAULT_REVERSAL_VOLUME_RATIO,
        exchange: Exchange | None = Exchange.HOSE,
    ) -> None:
        self._quote_service = quote_service
        self._top_n = top_n
        self._momentum_min_pct = momentum_min_pct
        self._breakout_min_pct = breakout_min_pct
        self._breakout_volume_ratio = breakout_volume_ratio
        self._volume_surge_ratio = volume_surge_ratio
        self._reversal_max_pct = reversal_max_pct
        self._reversal_volume_ratio = reversal_volume_ratio
        # Restrict scan universe to a single exchange (default HOSE).
        # None = scan all three exchanges (legacy behaviour).
        self._exchange = exchange

    async def run(self) -> ScreenResult:
        """Fetch quotes for all registry tickers and return ranked candidates.

        Returns ScreenResult with empty candidates list if registry is empty
        or bulk fetch fails. Never raises.
        """
        from src.market.registry import registry

        start = time.monotonic()
        now = datetime.now(timezone.utc)
        trading_date = now.strftime("%Y-%m-%d")

        # Apply exchange filter — default HOSE only, None = full registry.
        if self._exchange is not None:
            tickers = [s.ticker for s in registry.list_by_exchange(self._exchange)]
        else:
            tickers = registry.all_tickers()

        if not tickers:
            logger.warning(
                "opportunity_screen.no_tickers_in_registry",
                exchange=self._exchange.value if self._exchange else "ALL",
            )
            return ScreenResult(
                scanned_at=now,
                candidates=[],
                total_tickers_scanned=0,
                duration_seconds=0.0,
                trading_date=trading_date,
            )

        logger.info(
            "opportunity_screen.start",
            exchange=self._exchange.value if self._exchange else "ALL",
            total_tickers=len(tickers),
        )

        # Bulk fetch — single round-trip
        try:
            quotes = await self._quote_service.get_bulk_quotes(tickers)  # type: ignore[union-attr]
        except Exception as exc:
            logger.error("opportunity_screen.bulk_fetch_failed", error=str(exc))
            return ScreenResult(
                scanned_at=now,
                candidates=[],
                total_tickers_scanned=len(tickers),
                duration_seconds=time.monotonic() - start,
                trading_date=trading_date,
            )

        candidates: list[ScreenCandidate] = []
        for quote in quotes:
            candidate = self._screen_quote(quote)
            if candidate is not None:
                candidates.append(candidate)

        # Sort by composite_score DESC, then change_pct DESC as tiebreaker
        candidates.sort(key=lambda c: (-c.composite_score, -c.change_pct))
        top_candidates = candidates[: self._top_n]

        duration = time.monotonic() - start
        logger.info(
            "opportunity_screen.done",
            scanned=len(tickers),
            candidates_found=len(candidates),
            top_n=len(top_candidates),
            duration_seconds=round(duration, 3),
        )

        return ScreenResult(
            scanned_at=now,
            candidates=top_candidates,
            total_tickers_scanned=len(tickers),
            duration_seconds=round(duration, 3),
            trading_date=trading_date,
        )

    def _screen_quote(self, quote: object) -> ScreenCandidate | None:
        """Apply all screens to a single quote. Returns None if no criterion matched."""
        ticker: str = getattr(quote, "ticker", "")
        change_pct: float = float(getattr(quote, "change_pct", 0.0))
        current_price: float = float(getattr(quote, "price", 0.0))
        volume_ratio: float = float(getattr(quote, "volume_ratio", 1.0))

        if not ticker or current_price <= 0:
            return None

        criteria: list[str] = []

        # Screen 1: MOMENTUM
        if change_pct >= self._momentum_min_pct:
            criteria.append("MOMENTUM")

        # Screen 2: BREAKOUT (price + volume confirmation)
        if change_pct >= self._breakout_min_pct and volume_ratio >= self._breakout_volume_ratio:
            criteria.append("BREAKOUT")

        # Screen 3: VOLUME_SURGE (unusual activity regardless of direction)
        if volume_ratio >= self._volume_surge_ratio:
            criteria.append("VOLUME_SURGE")

        # Screen 4: REVERSAL_WATCH (oversold bounce candidate)
        if change_pct <= self._reversal_max_pct and volume_ratio >= self._reversal_volume_ratio:
            criteria.append("REVERSAL_WATCH")

        if not criteria:
            return None

        # Scoring
        raw_breakout = change_pct / BREAKOUT_SCORE_CAP_PCT
        volume_bonus = min(0.2, (volume_ratio - 1.0) * 0.1)  # up to +0.2 for volume
        breakout_score = min(1.0, max(0.0, raw_breakout + volume_bonus))

        momentum_score = min(1.0, max(0.0, change_pct / MOMENTUM_SCORE_CAP_PCT))

        composite_score = round(0.6 * breakout_score + 0.4 * momentum_score, 3)

        return ScreenCandidate(
            ticker=ticker,
            current_price=current_price,
            change_pct=change_pct,
            volume_ratio=volume_ratio,
            breakout_score=round(breakout_score, 3),
            momentum_score=round(momentum_score, 3),
            composite_score=composite_score,
            screen_criteria=criteria,
        )


# ── job function ─────────────────────────────────────────────────────────────

async def run_opportunity_screen_job(quote_service: object) -> ScreenResult:
    """Pure async job function — no Discord imports, testable in isolation.

    After the scan completes, emits OpportunityScreenCompletedEvent via
    EventBus with dedup_key='daily' and a 60-minute dedup window.
    This prevents duplicate AI analysis if the job is triggered multiple
    times within the same market session.

    Args:
        quote_service: QuoteService instance (duck-typed).

    Returns:
        ScreenResult — always returns, never raises.
    """
    from src.platform.event_bus import get_event_bus
    from src.platform.events import OpportunityScreenCompletedEvent

    svc = OpportunityScreenService(quote_service)

    try:
        result = await svc.run()
    except Exception as exc:
        logger.error("opportunity_screen_job.unexpected_error", error=str(exc))
        from datetime import datetime, timezone
        from src.market.opportunity_screen_service import ScreenResult
        result = ScreenResult(
            scanned_at=datetime.now(timezone.utc),
            candidates=[],
            total_tickers_scanned=0,
            duration_seconds=0.0,
            trading_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )

    # Emit event — dedup prevents double-firing within 60 min
    try:
        bus = get_event_bus()
        event = OpportunityScreenCompletedEvent(
            candidates_found=len(result.candidates),
            top_symbol=result.top_ticker,
            screen_criteria=result.screen_criteria_summary,
            candidates_payload=tuple(
                c.format_for_prompt() for c in result.candidates
            ),
        )
        emitted = await bus.publish(event, dedup_key="daily")
        if emitted:
            logger.info(
                "opportunity_screen_job.event_emitted",
                candidates_found=len(result.candidates),
                top_symbol=result.top_ticker,
                event_id=event.event_id,
            )
        else:
            logger.debug("opportunity_screen_job.event_deduped")
    except Exception as exc:
        logger.warning("opportunity_screen_job.event_emit_failed", error=str(exc))

    return result
