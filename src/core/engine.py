"""
Intelligence Engine — core orchestration cycle.
Owner: core segment.

No Discord logic. No DB models. No segment-specific imports at module level.

Input:  triggered via IntelligenceEngineListener (EventBus)
Output: EngineVerdict → emitted as IntelligenceEngineCompletedEvent

Wave 1: deterministic heuristic verdict (zero AI cost) — always available as fallback.
Wave 2: AI synthesis via IntelligenceVerdictAgent (ai segment).
         Activated when verdict_agent is passed to run_cycle().
         Falls back to Wave 1 if AI returns NO_ACTION or confidence too low.
Wave 3: signal_engine_summary from IntelligenceEngineRequestedEvent injected
         into build_snapshot() for richer AI prompt context.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from src.core import schemas, signals, snapshot
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.ai.agents.intelligence_verdict import IntelligenceVerdictAgent

logger = get_logger(__name__)

_CONFIDENCE_DISPATCH_THRESHOLD = 0.55
_HIGH_URGENCY_THRESHOLD         = 0.70


def _derive_verdict_heuristic(
    ranked: list[schemas.RankedSignal],
    snap: schemas.SystemSnapshot,
) -> tuple[
    Literal["BUY_SIGNAL", "SELL_SIGNAL", "HOLD", "REVIEW_THESIS", "RISK_ALERT", "WATCH", "NO_ACTION"],
    float,
]:
    """Wave 1 heuristic verdict — fully deterministic, zero AI cost."""
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
    signal_engine_summary: str = "",
    verdict_agent: Any | None = None,
) -> schemas.EngineVerdict | None:
    """
    Main engine cycle.

    Args:
        user_id:                Owner of this cycle.
        trigger_source:         What triggered this run.
        priority:               "high" bypasses the confidence gate.
        context_hint:           Optional freeform hint injected into reasoning.
        signal_engine_summary:  Narrative from SignalEngineCompletedEvent, injected
                                into snapshot so AI verdict prompt has richer context.
        verdict_agent:          Optional IntelligenceVerdictAgent (ai segment).
                                Wave 2 AI path when provided; Wave 1 heuristic fallback.

    Returns:
        EngineVerdict if threshold met, else None.
    """
    logger.info(
        "engine.cycle_start",
        user_id=user_id,
        trigger_source=trigger_source,
        priority=priority,
        wave="2_ai" if verdict_agent is not None else "1_heuristic",
        has_signal_summary=bool(signal_engine_summary),
    )

    try:
        snap = await snapshot.build_snapshot(
            user_id,
            trigger_source=trigger_source,
            signal_engine_summary=signal_engine_summary,
        )
    except Exception as exc:
        logger.error("engine.snapshot_failed", error=str(exc))
        return None

    ranked = signals.rank_signals(snap)

    if verdict_agent is not None:
        ai_out = await verdict_agent.run(snap, ranked)

        if ai_out.verdict != "NO_ACTION" and (
            ai_out.confidence >= _CONFIDENCE_DISPATCH_THRESHOLD or priority == "high"
        ):
            logger.info(
                "engine.ai_verdict_accepted",
                verdict=ai_out.verdict,
                confidence=ai_out.confidence,
            )
            return schemas.EngineVerdict(
                verdict=ai_out.verdict,
                confidence=ai_out.confidence,
                risk_signals=ai_out.risk_signals,
                next_watch_items=ai_out.next_watch_items,
                action=ai_out.action,
                reasoning_summary=ai_out.reasoning_summary,
                top_signals=ranked[:5],
                trigger_source=trigger_source,
            )

        logger.info(
            "engine.ai_verdict_fallback",
            ai_verdict=ai_out.verdict,
            ai_confidence=ai_out.confidence,
            reason="no_action_or_below_threshold",
        )

    verdict_label, confidence = _derive_verdict_heuristic(ranked, snap)

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
        f"[heuristic] Trigger: {trigger_source}. "
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
