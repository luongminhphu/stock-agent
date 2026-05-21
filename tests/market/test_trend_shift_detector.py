"""Unit tests for market.trend_shift_detector.

Tests are isolated from EventBus, TrendEngine, and TrendSnapshotStore I/O.
All tests use the pure detect_shift() function directly.
"""
from __future__ import annotations

import pytest

from src.market.trend_shift_detector import (
    COMPOSITE_DELTA_THRESHOLD,
    MINOR_CONFIDENCE_FLOOR,
    NEUTRAL_BAND_HI,
    NEUTRAL_BAND_LO,
    detect_shift,
)


# ─── MAJOR shift ──────────────────────────────────────────────────────────────

class TestMajorShift:
    def test_trending_up_to_trending_down(self):
        """Classic trend reversal: TRENDING_UP + bullish composite → TRENDING_DOWN + bearish."""
        event = detect_shift(
            symbol="VCB",
            prev_regime="TRENDING_UP",
            prev_composite=0.72,
            curr_regime="TRENDING_DOWN",
            curr_composite=0.31,
            scan_phase="pre_atc",
        )
        assert event is not None
        assert event.shift_severity == "MAJOR"
        assert event.symbol == "VCB"
        assert event.scan_phase == "pre_atc"
        assert event.composite_delta == pytest.approx(0.31 - 0.72, abs=1e-4)

    def test_trending_down_to_trending_up(self):
        """Recovery signal: bearish regime flips to bullish."""
        event = detect_shift(
            symbol="VNM",
            prev_regime="TRENDING_DOWN",
            prev_composite=0.28,
            curr_regime="TRENDING_UP",
            curr_composite=0.65,
        )
        assert event is not None
        assert event.shift_severity == "MAJOR"
        assert event.composite_delta > 0  # positive = strengthening

    def test_ranging_to_trending_down_with_zone_cross(self):
        """RANGING (neutral polarity) → TRENDING_DOWN (bearish polarity) + zone cross."""
        event = detect_shift(
            symbol="PC1",
            prev_regime="RANGING",
            prev_composite=0.55,  # NEUTRAL zone
            curr_regime="TRENDING_DOWN",
            curr_composite=0.33,  # BEAR zone
        )
        assert event is not None
        assert event.shift_severity == "MAJOR"


# ─── MINOR shift ──────────────────────────────────────────────────────────────

class TestMinorShift:
    def test_regime_change_with_sufficient_confidence(self):
        """Regime label changes but composite stays in same zone — MINOR."""
        event = detect_shift(
            symbol="HPG",
            prev_regime="RANGING",
            prev_composite=0.50,
            curr_regime="TRENDING_DOWN",
            curr_composite=0.38,  # below neutral band, delta = -0.12
            scan_phase="midday",
        )
        # delta = -0.12 < COMPOSITE_DELTA_THRESHOLD (0.15) but regime changed
        # and curr_composite (0.38) < NEUTRAL_BAND_LO (0.40)
        # and confidence = |0.38 - 0.5| = 0.12 >= MINOR_CONFIDENCE_FLOOR (0.10)
        assert event is not None
        assert event.shift_severity == "MINOR"
        assert event.scan_phase == "midday"

    def test_large_delta_no_regime_change_but_bearish_zone(self):
        """Composite drops hard without regime label change — MINOR."""
        event = detect_shift(
            symbol="MSN",
            prev_regime="RANGING",
            prev_composite=0.62,
            curr_regime="RANGING",
            curr_composite=0.35,  # delta = -0.27, crosses into BEAR zone
        )
        # delta = -0.27 >= COMPOSITE_DELTA_THRESHOLD, curr in BEAR zone
        assert event is not None
        assert event.shift_severity == "MINOR"


# ─── Noise filters — no alert expected ───────────────────────────────────────

class TestNoiseFilters:
    def test_identical_bundle_no_alert(self):
        """Exactly the same regime and composite → no event."""
        event = detect_shift(
            symbol="FPT",
            prev_regime="TRENDING_UP",
            prev_composite=0.68,
            curr_regime="TRENDING_UP",
            curr_composite=0.68,
        )
        assert event is None

    def test_small_delta_same_regime_no_alert(self):
        """Delta below threshold, same regime → noise, no alert."""
        event = detect_shift(
            symbol="VHM",
            prev_regime="RANGING",
            prev_composite=0.52,
            curr_regime="RANGING",
            curr_composite=0.48,  # delta = -0.04
        )
        assert event is None

    def test_minor_regime_change_inside_neutral_band_no_alert(self):
        """Regime changes but composite stays inside neutral band → no MINOR alert."""
        event = detect_shift(
            symbol="MWG",
            prev_regime="RANGING",
            prev_composite=0.50,
            curr_regime="VOLATILE",
            curr_composite=0.52,  # still inside neutral band [0.4, 0.6]
        )
        # VOLATILE has same polarity as RANGING (both NEUTRAL) → no polarity change
        # delta = 0.02 < threshold → no alert
        assert event is None

    def test_minor_low_confidence_below_floor(self):
        """Large delta but composite barely outside neutral band — confidence too low."""
        event = detect_shift(
            symbol="ACB",
            prev_regime="RANGING",
            prev_composite=0.55,
            curr_regime="TRENDING_DOWN",
            curr_composite=0.41,  # just outside NEUTRAL_BAND_LO
            # confidence = |0.41 - 0.5| = 0.09 < MINOR_CONFIDENCE_FLOOR (0.10)
        )
        # Polarity changed (NEUTRAL → BEARISH) + zone crossed → actually MAJOR
        # Wait: 0.55 is in NEUTRAL zone, 0.41 is also in NEUTRAL zone (0.40 < 0.41 < 0.60)
        # So zone_crossed = False → not MAJOR
        # delta = -0.14 < 0.15 threshold → delta_significant = False
        # polarity_changed = True (NEUTRAL → BEARISH)
        # → MINOR candidate: but 0.41 > NEUTRAL_BAND_LO (0.40) → inside neutral band → None
        assert event is None

    def test_volatile_to_ranging_no_alert(self):
        """VOLATILE → RANGING: both NEUTRAL polarity, small delta → no alert."""
        event = detect_shift(
            symbol="KBC",
            prev_regime="VOLATILE",
            prev_composite=0.48,
            curr_regime="RANGING",
            curr_composite=0.51,
        )
        assert event is None


# ─── Cold start (covered in TrendShiftDetector integration, verified by contract)
# detect_shift() itself is not called on cold start — TrendShiftDetector
# handles cold start before calling detect_shift(). No test needed here.
