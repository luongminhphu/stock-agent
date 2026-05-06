"""
Signal Engine — Watchlist V2
Owner: watchlist segment.

Classifies raw ScanSignal into typed SignalReports.
Does NOT persist anything. Does NOT call AI. Does NOT emit events.
Pure domain logic — takes ScanSignal (duck-typed), returns list[SignalReport].

Signal taxonomy (aligned with platform.events SignalDetectedEvent):
  BREAKOUT           — price vượt kháng cự + volume spike
  TREND_REVERSAL     — giá đảo chiều sau chuỗi ngược xu hướng  (placeholder — Wave 3)
  STRONG_MOVE        — biến động mạnh đơn thuần (>=3%) không đủ criteria khác
  ALERT_TRIGGERED    — alert do user tự cài đã bị kích hoạt
  THESIS_DIVERGENCE  — giá đi ngược thesis đang active (Wave 3 — needs thesis context)
  RISK_SPIKE         — tín hiệu rủi ro (sharp downside, stop-loss zone)

Thresholds are tunable via constructor — defaults are conservative
for HOSE/HNX daily price action.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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
    detected_at: datetime = field(default_factory=datetime.utcnow)
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
        breakout_change_pct   >= 4.0%  + volume spike  → BREAKOUT
        breakout_volume_ratio >= 1.5x  (vs average)    → required for BREAKOUT
        risk_spike_change_pct <= -4.0%                 → RISK_SPIKE
        strong_move_pct       >= 3.0%  (either dir)    → STRONG_MOVE fallback
    """

    def __init__(
        self,
        breakout_change_pct: float = 4.0,
        breakout_volume_ratio: float = 1.5,
        strong_move_pct: float = 3.0,
        risk_spike_change_pct: float = -4.0,
    ) -> None:
        self._breakout_change_pct = breakout_change_pct
        self._breakout_volume_ratio = breakout_volume_ratio
        self._strong_move_pct = strong_move_pct
        self._risk_spike_change_pct = risk_spike_change_pct

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

        # ─ Optional: blend AI credibility score into confidence ─────────
        if credibility is not None:
            cred_score = getattr(credibility, "score", None)
            if cred_score is not None:
                for r in reports:
                    blended = round(r.confidence * 0.7 + float(cred_score) * 0.3, 3)
                    r.confidence = blended
                    r.metadata["credibility_score"] = cred_score

        return reports
