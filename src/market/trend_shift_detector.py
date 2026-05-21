"""TrendShiftDetector — proactive trend change detection for portfolio symbols.

Owner: market segment.

Responsibility:
  - Accept a list of portfolio symbols + their last TechnicalSignalBundle
    snapshots (via TrendSnapshotStore).
  - Run TrendEngine.run_for_symbols() to get current bundles.
  - Compare current vs previous: detect MAJOR / MINOR shifts.
  - Emit TrendShiftEvent via EventBus for each detected shift.
  - Always update the snapshot store with the current bundle (baseline for
    the next scan, regardless of whether a shift was detected).

Boundary (hard rules):
  - NEVER imports from bot, discord, briefing, or api.
  - NEVER imports portfolio.repository directly — receives symbols as a
    plain list[str] from the caller (bot scheduler via portfolio service).
  - Publishes TrendShiftEvent to EventBus; never calls Discord directly.
  - Reads/writes TrendSnapshotStore (readmodel public API) — not raw DB.

Shift classification:
  MAJOR:  regime changed AND composite crossed the 0.4/0.6 threshold boundary
          (e.g. was BULLISH zone ≥ 0.6, now BEARISH zone ≤ 0.4).
  MINOR:  regime changed OR composite moved > COMPOSITE_DELTA_THRESHOLD,
          AND confidence (composite distance from 0.5) >= MINOR_CONFIDENCE_FLOOR.

Noise filters (prevent alert fatigue):
  - Cold start: no previous snapshot → save current, skip alert.
  - Identical regime + composite delta below COMPOSITE_DELTA_THRESHOLD → no alert.
  - MINOR shift only fires when |composite_delta| >= COMPOSITE_DELTA_THRESHOLD
    AND the current composite is outside the neutral band (0.4–0.6).

Scan phases (set by caller):
  morning  — 09:05 ICT, first scan after opening
  midday   — 11:00 ICT, mid-session check
  pre_atc  — 14:10 ICT, last continuous trading check before ATC (14:30)
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.platform.event_bus import get_event_bus
from src.platform.events import TrendShiftEvent
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.market.trend_engine import TechnicalSignalBundle, TrendEngine
    from src.readmodel.trend_snapshot_store import TrendSnapshotStore

logger = get_logger(__name__)

# ─── Tunable thresholds ──────────────────────────────────────────────────────
# Composite must move by at least this much to qualify as a meaningful shift.
COMPOSITE_DELTA_THRESHOLD: float = 0.15

# MINOR shifts only fire when composite is outside this neutral band.
NEUTRAL_BAND_LO: float = 0.4
NEUTRAL_BAND_HI: float = 0.6

# MINOR confidence floor: |composite − 0.5| must be >= this value.
MINOR_CONFIDENCE_FLOOR: float = 0.10


# ─── Regime direction helpers ─────────────────────────────────────────────────

_BULLISH_REGIMES = frozenset({"TRENDING_UP"})
_BEARISH_REGIMES = frozenset({"TRENDING_DOWN"})


def _regime_polarity(regime: str) -> str:
    """Collapse the 4 regime labels into 3 polarity buckets.

    Prevents RANGING → VOLATILE from triggering a false MAJOR shift.
    TRENDING_UP   → BULLISH
    TRENDING_DOWN → BEARISH
    RANGING       → NEUTRAL
    VOLATILE      → NEUTRAL  (volatility expansion ≠ directional shift)
    """
    if regime in _BULLISH_REGIMES:
        return "BULLISH"
    if regime in _BEARISH_REGIMES:
        return "BEARISH"
    return "NEUTRAL"


def _composite_zone(composite: float) -> str:
    """Classify composite score into three zones."""
    if composite >= NEUTRAL_BAND_HI:
        return "BULL"
    if composite <= NEUTRAL_BAND_LO:
        return "BEAR"
    return "NEUTRAL"


# ─── Core detection logic (pure function — easy to unit-test) ─────────────────

def detect_shift(
    symbol: str,
    prev_regime: str,
    prev_composite: float,
    curr_regime: str,
    curr_composite: float,
    scan_phase: str = "",
) -> TrendShiftEvent | None:
    """Compare previous and current bundle scalars; return TrendShiftEvent or None.

    Pure function — no I/O, no imports from EventBus.
    Designed to be called from TrendShiftDetector._compare() and unit tests.

    Returns None when:
      - Regime polarity is identical AND composite delta < threshold (no meaningful change)
      - MINOR candidate but composite is inside neutral band (noise filter)
      - MINOR candidate but confidence < MINOR_CONFIDENCE_FLOOR
    """
    delta = round(curr_composite - prev_composite, 4)
    prev_polarity = _regime_polarity(prev_regime)
    curr_polarity = _regime_polarity(curr_regime)

    polarity_changed = prev_polarity != curr_polarity
    zone_crossed = _composite_zone(prev_composite) != _composite_zone(curr_composite)
    delta_significant = abs(delta) >= COMPOSITE_DELTA_THRESHOLD

    # ── MAJOR: polarity flipped AND composite crossed a zone boundary ──────
    if polarity_changed and zone_crossed:
        logger.debug(
            "trend_shift.major_detected",
            symbol=symbol,
            prev_regime=prev_regime,
            curr_regime=curr_regime,
            delta=delta,
        )
        return TrendShiftEvent(
            symbol=symbol,
            previous_regime=prev_regime,
            current_regime=curr_regime,
            previous_composite=prev_composite,
            current_composite=curr_composite,
            composite_delta=delta,
            shift_severity="MAJOR",
            scan_phase=scan_phase,
        )

    # ── MINOR: regime changed OR delta significant, but not both ──────────
    if not (polarity_changed or delta_significant):
        return None  # no meaningful movement

    # noise filter: composite must be outside neutral band
    if not (curr_composite <= NEUTRAL_BAND_LO or curr_composite >= NEUTRAL_BAND_HI):
        return None

    # noise filter: minimum confidence (distance from neutral 0.5)
    confidence = abs(curr_composite - 0.5)
    if confidence < MINOR_CONFIDENCE_FLOOR:
        return None

    logger.debug(
        "trend_shift.minor_detected",
        symbol=symbol,
        prev_regime=prev_regime,
        curr_regime=curr_regime,
        delta=delta,
    )
    return TrendShiftEvent(
        symbol=symbol,
        previous_regime=prev_regime,
        current_regime=curr_regime,
        previous_composite=prev_composite,
        current_composite=curr_composite,
        composite_delta=delta,
        shift_severity="MINOR",
        scan_phase=scan_phase,
    )


# ─── TrendShiftDetector ───────────────────────────────────────────────────────

class TrendShiftDetector:
    """Scan a list of portfolio symbols; detect and publish TrendShiftEvents.

    Usage (from bot scheduler — Wave 2)::

        detector = TrendShiftDetector(
            trend_engine=trend_engine,
            snapshot_store=trend_snapshot_store,
        )
        shifts = await detector.scan(symbols=["VCB", "VNM", "PC1"], scan_phase="pre_atc")
        # TrendShiftEvents already published to EventBus by scan().
        # shifts list is returned for logging / metrics only.
    """

    def __init__(
        self,
        trend_engine: "TrendEngine",
        snapshot_store: "TrendSnapshotStore",
    ) -> None:
        self._engine = trend_engine
        self._store = snapshot_store

    async def scan(
        self,
        symbols: list[str],
        scan_phase: str = "",
    ) -> list[TrendShiftEvent]:
        """Run trend scan for all symbols; return list of detected shift events.

        For each symbol:
          1. Fetch current TechnicalSignalBundle via TrendEngine.
          2. Compare with last snapshot in TrendSnapshotStore.
          3. If shift detected → publish TrendShiftEvent to EventBus.
          4. Always update snapshot store with current bundle.

        Errors per symbol are isolated — one bad OHLCV fetch does not abort
        the entire scan.
        """
        if not symbols:
            return []

        upper_symbols = [s.upper() for s in symbols]
        logger.info(
            "trend_shift_detector.scan_start",
            symbols=upper_symbols,
            scan_phase=scan_phase,
        )

        # Fetch all bundles concurrently
        bundles = await self._engine.run_for_symbols(upper_symbols)

        shifts: list[TrendShiftEvent] = []
        for bundle in bundles:
            shift = await self._process_symbol(bundle, scan_phase)
            if shift is not None:
                shifts.append(shift)

        logger.info(
            "trend_shift_detector.scan_complete",
            symbols_scanned=len(bundles),
            shifts_detected=len(shifts),
            scan_phase=scan_phase,
        )
        return shifts

    async def _process_symbol(
        self,
        bundle: "TechnicalSignalBundle",
        scan_phase: str,
    ) -> TrendShiftEvent | None:
        """Compare bundle with snapshot; publish shift event if found; update snapshot."""
        symbol = bundle.symbol
        current_regime = bundle.regime
        current_composite = bundle.composite

        prev_entry = self._store.get_last(symbol)

        if prev_entry is None:
            # Cold start: no baseline yet — save and wait for next scan.
            logger.debug(
                "trend_shift_detector.cold_start",
                symbol=symbol,
                regime=current_regime,
                composite=current_composite,
            )
            self._store.save(symbol, bundle.model_dump())
            return None

        prev_bundle = prev_entry["bundle"]
        prev_regime = prev_bundle.get("regime", "RANGING")
        prev_composite = float(prev_bundle.get("composite", 0.5))

        shift = detect_shift(
            symbol=symbol,
            prev_regime=prev_regime,
            prev_composite=prev_composite,
            curr_regime=current_regime,
            curr_composite=current_composite,
            scan_phase=scan_phase,
        )

        # Always update snapshot — baseline for next scan
        self._store.save(symbol, bundle.model_dump())

        if shift is None:
            return None

        # Publish to EventBus (best-effort — never blocks snapshot update)
        try:
            bus = get_event_bus()
            await bus.publish(shift)
            logger.info(
                "trend_shift_detector.event_published",
                symbol=symbol,
                severity=shift.shift_severity,
                prev_regime=shift.previous_regime,
                curr_regime=shift.current_regime,
                delta=shift.composite_delta,
                scan_phase=scan_phase,
            )
        except Exception as exc:
            logger.error(
                "trend_shift_detector.publish_failed",
                symbol=symbol,
                error=str(exc),
            )

        return shift
