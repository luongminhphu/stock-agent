"""
Signal ranker — transforms SystemSnapshot into a prioritised RankedSignal list.
Owner: core segment. No AI calls. Deterministic scoring.
"""
from __future__ import annotations

from src.core.schemas import RankedSignal, SystemSnapshot

# Base weights — can be externalised to config in a later wave
_WEIGHTS: dict[str, float] = {
    "portfolio_risk":     1.0,   # risk breach always highest priority
    "thesis_invalidated": 0.9,
    "watchlist_triggered": 0.7,
    "thesis_drift":       0.6,
    "thesis_stale":       0.5,
    "volume_spike":       0.5,
    "market_trend_shift": 0.4,
    "opportunity":        0.3,
}

_CROSS_SEGMENT_AMPLIFIER = 1.2   # score boost when ≥ 3 sources fire simultaneously
_MAX_SCORE               = 1.0


def rank_signals(snapshot: SystemSnapshot) -> list[RankedSignal]:
    """Return RankedSignal list sorted by urgency_score descending."""
    signals: list[RankedSignal] = []

    if snapshot.portfolio.risk_breach_count > 0:
        signals.append(RankedSignal(
            source="portfolio",
            description=f"{snapshot.portfolio.risk_breach_count} vị thế vượt ngưỡng rủi ro",
            urgency_score=_WEIGHTS["portfolio_risk"],
            raw_count=snapshot.portfolio.risk_breach_count,
        ))

    if snapshot.thesis.invalidated_count > 0:
        signals.append(RankedSignal(
            source="thesis",
            description=f"{snapshot.thesis.invalidated_count} thesis bị invalidate",
            urgency_score=_WEIGHTS["thesis_invalidated"],
            raw_count=snapshot.thesis.invalidated_count,
        ))

    if snapshot.watchlist.triggered_alert_count > 0:
        tickers = ", ".join(snapshot.watchlist.top_tickers[:3])
        signals.append(RankedSignal(
            source="watchlist",
            description=(
                f"{snapshot.watchlist.triggered_alert_count} alert kích hoạt"
                + (f" ({tickers})" if tickers else "")
            ),
            urgency_score=_WEIGHTS["watchlist_triggered"],
            raw_count=snapshot.watchlist.triggered_alert_count,
        ))

    if snapshot.thesis.drift_detected_count > 0:
        signals.append(RankedSignal(
            source="thesis",
            description=f"{snapshot.thesis.drift_detected_count} thesis có price drift",
            urgency_score=_WEIGHTS["thesis_drift"],
            raw_count=snapshot.thesis.drift_detected_count,
        ))

    if snapshot.thesis.stale_count > 0:
        signals.append(RankedSignal(
            source="thesis",
            description=f"{snapshot.thesis.stale_count} thesis cần review (stale > 3 ngày)",
            urgency_score=_WEIGHTS["thesis_stale"],
            raw_count=snapshot.thesis.stale_count,
        ))

    if snapshot.watchlist.has_volume_spike:
        signals.append(RankedSignal(
            source="watchlist",
            description="Phát hiện volume spike bất thường",
            urgency_score=_WEIGHTS["volume_spike"],
        ))

    if snapshot.market.trend_shift_count > 0:
        signals.append(RankedSignal(
            source="market",
            description=f"{snapshot.market.trend_shift_count} symbol đổi xu hướng",
            urgency_score=_WEIGHTS["market_trend_shift"],
            raw_count=snapshot.market.trend_shift_count,
        ))

    if snapshot.market.opportunity_count > 0:
        signals.append(RankedSignal(
            source="market",
            description=f"{snapshot.market.opportunity_count} cơ hội thị trường",
            urgency_score=_WEIGHTS["opportunity"],
            raw_count=snapshot.market.opportunity_count,
        ))

    # Cross-segment amplification: boost all scores when ≥ 3 sources fire
    unique_sources = {s.source for s in signals}
    if len(unique_sources) >= 3:
        for s in signals:
            s.urgency_score = min(_MAX_SCORE, s.urgency_score * _CROSS_SEGMENT_AMPLIFIER)

    return sorted(signals, key=lambda s: s.urgency_score, reverse=True)
