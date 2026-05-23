"""
Intelligence Engine — core orchestration cycle.
Owner: core segment.

No Discord logic. No DB models. No segment-specific imports at module level.

Input:  triggered via IntelligenceEngineListener (EventBus)
Output: EngineVerdict → emitted as IntelligenceEngineCompletedEvent

Wave 1: deterministic heuristic verdict (zero AI cost)
Wave 2: AI synthesis via ai segment (planned)
"""
from __future__ import annotations

from typing import Literal

from src.core import schemas, signals, snapshot
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Only dispatch when confidence meets this threshold (priority="high" bypasses)
_CONFIDENCE_DISPATCH_THRESHOLD = 0.55
# Minimum top-signal urgency for a directional verdict (BUY/SELL/RISK_ALERT)
_HIGH_URGENCY_THRESHOLD         = 0.70


def _derive_verdict(
    ranked: list[schemas.RankedSignal],
    snap: schemas.SystemSnapshot,
) -> tuple[
    Literal["BUY_SIGNAL", "SELL_SIGNAL", "HOLD", "REVIEW_THESIS", "RISK_ALERT", "WATCH", "NO_ACTION"],
    float,
]:
    """Heuristic verdict from top signals.
    Wave 1: fully deterministic.
    Wave 2: replace body with AI call via ai segment.
    """
    if not ranked:
        return "NO_ACTION", 0.3

    top = ranked[0]

    if top.source == "portfolio" and top.urgency_score >= 0.9:
        return "RISK_ALERT", min(0.95, top.urgency_score)

    if top.source == "thesis" and "invalidate" in top.description:
        return "REVIEW_THESIS", min(0.90, top.urgency_score + 0.10)

    if top.source == "watchlist" and snap.watchlist.triggered_alert_count >= 3:
        return "BUY_SIGNAL", min(0.85, top.urgency_score + 0.05)

    if top.urgency_score >= _HIGH_URGENCY_THRESHOLD:
        return "WATCH", top.urgency_score

    if len(ranked) >= 2:
        avg = sum(s.urgency_score for s in ranked[:3]) / min(3, len(ranked))
        return "HOLD", avg

    return "NO_ACTION", 0.3


def _build_action_text(
    verdict: str,
    ranked: list[schemas.RankedSignal],
    snap: schemas.SystemSnapshot,
) -> str:
    if verdict == "RISK_ALERT":
        return f"⚠️ Kiểm tra ngay {snap.portfolio.risk_breach_count} vị thế vượt ngưỡng rủi ro"
    if verdict == "REVIEW_THESIS":
        return f"📋 Review {snap.thesis.invalidated_count} thesis có dấu hiệu invalidation"
    if verdict == "BUY_SIGNAL":
        tickers = ", ".join(snap.watchlist.top_tickers[:3])
        return f"🟢 Cân nhắc hành động với: {tickers}"
    if verdict == "WATCH":
        sources = list({s.source for s in ranked[:3]})
        return f"👁 Theo dõi sát: tín hiệu từ {', '.join(sources)}"
    if verdict == "HOLD":
        return "⏸ Không có hành động cấp bách. Duy trì watchlist hiện tại"
    return "✅ Hệ thống bình thường, không cần hành động"


async def run_cycle(
    user_id: str,
    trigger_source: str,
    priority: str = "normal",
    context_hint: str | None = None,
) -> schemas.EngineVerdict | None:
    """
    Main engine cycle.
    Returns None if snapshot failed completely or confidence < threshold.
    """
    logger.info(
        "engine.cycle_start",
        user_id=user_id,
        trigger_source=trigger_source,
        priority=priority,
    )

    # 1. Build cross-segment state
    try:
        snap = await snapshot.build_snapshot(user_id, trigger_source=trigger_source)
    except Exception as exc:
        logger.error("engine.snapshot_failed", error=str(exc))
        return None

    # 2. Rank signals
    ranked = signals.rank_signals(snap)

    # 3. Derive verdict (Wave 1: heuristic; Wave 2: ai segment)
    verdict_label, confidence = _derive_verdict(ranked, snap)

    # 4. Confidence gate
    if confidence < _CONFIDENCE_DISPATCH_THRESHOLD and priority != "high":
        logger.info(
            "engine.below_threshold",
            verdict=verdict_label,
            confidence=confidence,
            threshold=_CONFIDENCE_DISPATCH_THRESHOLD,
        )
        return None

    action = _build_action_text(verdict_label, ranked, snap)
    risk_signals = [s.description for s in ranked if s.source in ("portfolio", "thesis")]
    next_watch_items = snap.watchlist.top_tickers[:5]

    reasoning = (
        f"Trigger: {trigger_source}. "
        f"Phase: {snap.market.market_phase}. "
        f"Top signals ({len(ranked)}): "
        + "; ".join(f"{s.source}={s.urgency_score:.2f}" for s in ranked[:3])
    )
    if context_hint:
        reasoning += f". Context hint: {context_hint}"

    engine_verdict = schemas.EngineVerdict(
        verdict=verdict_label,
        confidence=confidence,
        risk_signals=risk_signals,
        next_watch_items=next_watch_items,
        action=action,
        reasoning_summary=reasoning,
        top_signals=ranked[:5],
        trigger_source=trigger_source,
    )

    logger.info(
        "engine.cycle_done",
        verdict=verdict_label,
        confidence=confidence,
        signal_count=len(ranked),
        action=action,
    )
    return engine_verdict
