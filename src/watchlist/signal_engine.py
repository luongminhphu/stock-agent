"""
Signal Engine — Watchlist V2
Owner: watchlist segment.

Classifies raw ScanSignal into typed SignalReports.
Does NOT persist anything. Does NOT call AI. Does NOT emit events.
Pure domain logic — takes ScanSignal (duck-typed), returns list[SignalReport].

Signal taxonomy (aligned with platform.events SignalDetectedEvent):
  BREAKOUT           — price vượt kháng cự + volume spike
  TREND_REVERSAL     — giá đảo chiều sau chuỗi ngược xu hướng
  STRONG_MOVE        — biến động mạnh đơn thuần (>=3%) không đủ criteria khác
  ALERT_TRIGGERED    — alert do user tự cài đã bị kích hoạt
  THESIS_DIVERGENCE  — giá đi ngược thesis đang active
  RISK_SPIKE         — tín hiệu rủi ro (sharp downside, stop-loss zone)

Thresholds are tunable via constructor — defaults are conservative
for HOSE/HNX daily price action.

── Wave 1 enrichment (current) ──────────────────────────────────────────────
TREND_REVERSAL:
    Triggered khi change_pct >= reversal_bounce_pct VÀ scan_signal có
    prior_risk_spike=True (boolean, default False).
    ScanService inject field này bằng cách query SignalEventRepository:
        had_risk = await repo.has_recent(symbol, "RISK_SPIKE", hours=24)
        scan_signal.prior_risk_spike = had_risk
    Khi prior_risk_spike chưa được inject, engine skip silently.

THESIS_DIVERGENCE:
    Triggered khi scan_signal có thesis_direction="bull"|"bear" VÀ
    actual change_pct đi ngược chiều quá thesis_divergence_min_pct.
    ScanService inject field này bằng cách:
        item = watchlist_item_for(symbol)
        if item.thesis_id:
            thesis = await thesis_repo.get(item.thesis_id)
            scan_signal.thesis_direction = thesis.direction  # "bull" | "bear"
    Khi thesis_direction chưa được inject, engine skip silently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


# ── Signal Types ─────────────────────────────────────────────────────────────────

class SignalType:
    """Namespace for signal type constants (aligned with events.py)."""
    BREAKOUT = "BREAKOUT"
    TREND_REVERSAL = "TREND_REVERSAL"
    STRONG_MOVE = "STRONG_MOVE"
    ALERT_TRIGGERED = "ALERT_TRIGGERED"
    THESIS_DIVERGENCE = "THESIS_DIVERGENCE"
    RISK_SPIKE = "RISK_SPIKE"


@dataclass
class SignalReport:
    """
    Structured signal output from SignalEngine.
    One ScanSignal can produce zero or more SignalReports.
    """
    symbol: str
    signal_type: str                    # SignalType constant
    strength: float                     # 0.0 – 1.0 (magnitude of move/pattern)
    confidence: float                   # 0.0 – 1.0 (reliability of classification)
    source: str                         # "technical" | "alert" | "combined"
    description: str                    # human-readable — used in Discord / briefing
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        """Canonical dedup key for EventBus spam prevention."""
        return f"{self.symbol}:{self.signal_type}"

    def is_actionable(self) -> bool:
        """
        True if signal is strong + confident enough to trigger a push notification.
        Threshold: strength >= 0.5 AND confidence >= 0.6.
        """
        return self.strength >= 0.5 and self.confidence >= 0.6

    def __repr__(self) -> str:
        return (
            f"SignalReport({self.symbol!r}, type={self.signal_type!r}, "
            f"strength={self.strength:.2f}, confidence={self.confidence:.2f})"
        )


# ── Engine ─────────────────────────────────────────────────────────────────────

class SignalEngine:
    """
    Classifies a ScanSignal into zero or more SignalReports.

    Design:
    - Duck-typed input (avoids circular import with scan_service)
    - No side effects: pure classification, all IO handled by caller
    - Tunable thresholds for different market regimes

    Thresholds (HOSE/HNX defaults):
        breakout_change_pct        >= 4.0%  + volume spike  → BREAKOUT
        breakout_volume_ratio      >= 1.5x  (vs average)    → required for BREAKOUT
        risk_spike_change_pct      <= -4.0%                 → RISK_SPIKE
        strong_move_pct            >= 3.0%  (either dir)    → STRONG_MOVE fallback
        reversal_bounce_pct        >= 2.5%  + prior_risk_spike → TREND_REVERSAL
        thesis_divergence_min_pct  >= 2.0%  divergence      → THESIS_DIVERGENCE
    """

    def __init__(
        self,
        breakout_change_pct: float = 4.0,
        breakout_volume_ratio: float = 1.5,
        strong_move_pct: float = 3.0,
        risk_spike_change_pct: float = -4.0,
        reversal_bounce_pct: float = 2.5,
        thesis_divergence_min_pct: float = 2.0,
    ) -> None:
        self._breakout_change_pct = breakout_change_pct
        self._breakout_volume_ratio = breakout_volume_ratio
        self._strong_move_pct = strong_move_pct
        self._risk_spike_change_pct = risk_spike_change_pct
        self._reversal_bounce_pct = reversal_bounce_pct
        self._thesis_divergence_min_pct = thesis_divergence_min_pct

    def evaluate(self, scan_signal: object) -> list[SignalReport]:
        """
        Classify a ScanSignal into zero or more SignalReports.

        Expected attributes on scan_signal (duck-typed):
            ticker: str
            current_price: float
            change_pct: float
            triggered_alerts: list  (each with .condition_type.value)
            credibility: optional   (with .score float attribute)
            _volume_ratio: float    (optional, defaults to 1.0)

        Wave 1 optional enrichment fields (injected by ScanService):
            prior_risk_spike: bool      — True nếu symbol có RISK_SPIKE trong 24h qua
            thesis_direction: str|None  — "bull" | "bear" từ active thesis

        Returns:
            List of SignalReport. Empty list = no signal this tick.
        """
        reports: list[SignalReport] = []

        symbol: str = scan_signal.ticker  # type: ignore[union-attr]
        change_pct: float = scan_signal.change_pct  # type: ignore[union-attr]
        current_price: float = scan_signal.current_price  # type: ignore[union-attr]
        volume_ratio: float = getattr(scan_signal, "_volume_ratio", 1.0)
        triggered_alerts: list = getattr(scan_signal, "triggered_alerts", [])
        credibility = getattr(scan_signal, "credibility", None)

        # Wave 1 enrichment fields — safe defaults when not injected
        prior_risk_spike: bool = getattr(scan_signal, "prior_risk_spike", False)
        thesis_direction: str | None = getattr(scan_signal, "thesis_direction", None)

        # ─ Rule 1: Alert Triggered (highest confidence — user-defined) ──────
        if triggered_alerts:
            alert_count = len(triggered_alerts)
            alert_types = list({
                getattr(a.condition_type, "value", str(a.condition_type))
                for a in triggered_alerts
            })
            reports.append(SignalReport(
                symbol=symbol,
                signal_type=SignalType.ALERT_TRIGGERED,
                strength=min(1.0, 0.5 + alert_count * 0.15),
                confidence=0.95,
                source="alert",
                description=(
                    f"{alert_count} alert {'triggered' if alert_count == 1 else 'triggered'} "
                    f"trên {symbol}: {', '.join(alert_types)}"
                ),
                metadata={
                    "alert_count": alert_count,
                    "alert_types": alert_types,
                    "price": current_price,
                },
            ))

        # ─ Rule 2: Breakout (price spike + volume confirmation) ───────────
        if (
            change_pct >= self._breakout_change_pct
            and volume_ratio >= self._breakout_volume_ratio
        ):
            strength = min(1.0, change_pct / 10.0)
            confidence = min(0.90, 0.60 + (volume_ratio - 1.5) * 0.10)
            reports.append(SignalReport(
                symbol=symbol,
                signal_type=SignalType.BREAKOUT,
                strength=round(strength, 3),
                confidence=round(confidence, 3),
                source="technical",
                description=(
                    f"{symbol} breakout: +{change_pct:.1f}% "
                    f"với volume {volume_ratio:.1f}x trung bình"
                ),
                metadata={
                    "change_pct": change_pct,
                    "volume_ratio": volume_ratio,
                    "price": current_price,
                },
            ))

        # ─ Rule 3: Risk Spike (sharp downside move) ───────────────────
        elif change_pct <= self._risk_spike_change_pct:
            strength = min(1.0, abs(change_pct) / 10.0)
            reports.append(SignalReport(
                symbol=symbol,
                signal_type=SignalType.RISK_SPIKE,
                strength=round(strength, 3),
                confidence=0.80,
                source="technical",
                description=(
                    f"{symbol} risk spike: {change_pct:.1f}% — "
                    f"kiểm tra stop-loss và thesis"
                ),
                metadata={
                    "change_pct": change_pct,
                    "price": current_price,
                },
            ))

        # ─ Rule 4: Strong Move fallback (no volume confirmation) ───────
        elif abs(change_pct) >= self._strong_move_pct and not any(
            r.signal_type in (SignalType.BREAKOUT, SignalType.RISK_SPIKE)
            for r in reports
        ):
            is_up = change_pct > 0
            reports.append(SignalReport(
                symbol=symbol,
                signal_type=SignalType.STRONG_MOVE,
                strength=min(1.0, abs(change_pct) / 8.0),
                confidence=0.60,
                source="technical",
                description=(
                    f"{symbol} strong {'tăng' if is_up else 'giảm'}: {change_pct:+.1f}%"
                ),
                metadata={"change_pct": change_pct, "price": current_price},
            ))

        # ─ Rule 5: Trend Reversal (bounce after risk spike) ──────────────
        # Requires prior_risk_spike=True injected by ScanService (Wave 1).
        # When not injected, prior_risk_spike=False — rule skips silently.
        # Mutually exclusive with BREAKOUT (breakout is the stronger signal).
        if (
            prior_risk_spike
            and change_pct >= self._reversal_bounce_pct
            and not any(r.signal_type == SignalType.BREAKOUT for r in reports)
        ):
            strength = min(1.0, change_pct / 8.0)
            # Higher confidence when volume also elevated
            confidence = 0.65 + (0.10 if volume_ratio >= 1.3 else 0.0)
            reports.append(SignalReport(
                symbol=symbol,
                signal_type=SignalType.TREND_REVERSAL,
                strength=round(strength, 3),
                confidence=round(confidence, 3),
                source="technical",
                description=(
                    f"{symbol} reversal bounce: +{change_pct:.1f}% "
                    f"sau risk spike — theo dõi confirmation"
                ),
                metadata={
                    "change_pct": change_pct,
                    "volume_ratio": volume_ratio,
                    "price": current_price,
                    "trigger": "prior_risk_spike",
                },
            ))

        # ─ Rule 6: Thesis Divergence (price vs. thesis direction) ────────
        # Requires thesis_direction injected by ScanService (Wave 1).
        # When not injected, thesis_direction=None — rule skips silently.
        # Can co-exist with RISK_SPIKE (divergence + risk = double warning).
        if thesis_direction is not None:
            is_bull_thesis = thesis_direction == "bull"
            is_bear_thesis = thesis_direction == "bear"
            diverged = (
                (is_bull_thesis and change_pct <= -self._thesis_divergence_min_pct)
                or (is_bear_thesis and change_pct >= self._thesis_divergence_min_pct)
            )
            if diverged:
                divergence_magnitude = abs(change_pct)
                strength = min(1.0, divergence_magnitude / 8.0)
                # Higher confidence when divergence is large
                confidence = min(0.85, 0.60 + divergence_magnitude * 0.02)
                direction_label = "tăng" if change_pct > 0 else "giảm"
                reports.append(SignalReport(
                    symbol=symbol,
                    signal_type=SignalType.THESIS_DIVERGENCE,
                    strength=round(strength, 3),
                    confidence=round(confidence, 3),
                    source="combined",
                    description=(
                        f"{symbol} đi ngược thesis ({thesis_direction}): "
                        f"{direction_label} {abs(change_pct):.1f}% — xem xét lại luận điểm"
                    ),
                    metadata={
                        "change_pct": change_pct,
                        "price": current_price,
                        "thesis_direction": thesis_direction,
                        "divergence_pct": round(change_pct, 2),
                    },
                ))

        # ─ Optional: blend AI credibility score into confidence ─────────
        if credibility is not None:
            cred_score = getattr(credibility, "score", None)
            if cred_score is not None:
                for r in reports:
                    blended = round(r.confidence * 0.7 + float(cred_score) * 0.3, 3)
                    r.confidence = blended
                    r.metadata["credibility_score"] = cred_score

        return reports
