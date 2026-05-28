"""GlobalRiskSubscriber — event bus listener for IntelligenceEngineCompletedEvent.

Owner: readmodel segment.
Responsibility: listen to IntelligenceEngineCompletedEvent, project the
                verdict into GlobalRiskStore so that BriefingService and
                WatchlistScanService can read engine context at near-zero cost.

Registration::

    GlobalRiskSubscriber.register()

This is called once during bootstrap after the event bus is started.
The subscriber is stateless; GlobalRiskStore holds all state.
"""
from __future__ import annotations

from typing import Any

from src.platform.event_bus import get_event_bus
from src.platform.logging import get_logger
from src.readmodel.global_risk_store import GlobalRiskSnapshot, GlobalRiskStore

logger = get_logger(__name__)

# Event name to subscribe to.  Must match the name emitted by IntelligenceEngine.
_EVENT_NAME = "intelligence_engine.completed"


class GlobalRiskSubscriber:
    """Stateless subscriber that writes IE verdict into GlobalRiskStore.

    Call register() once at bootstrap.  Multiple register() calls are safe
    (idempotent guard via _registered class flag).
    """

    _registered: bool = False

    @classmethod
    def register(cls) -> None:
        if cls._registered:
            return
        bus = get_event_bus()
        bus.subscribe(_EVENT_NAME, cls._handle)
        cls._registered = True
        logger.info("global_risk_subscriber.registered", event=_EVENT_NAME)

    # ── handler ───────────────────────────────────────────────────────────

    @classmethod
    async def _handle(cls, event: Any) -> None:
        """Project IntelligenceEngineCompletedEvent payload into GlobalRiskStore.

        The event payload is expected to carry an EngineVerdict-like object
        or dict.  We access fields defensively so that schema evolution in
        core/engine.py does not break the readmodel subscriber.
        """
        try:
            verdict = _extract_verdict(event)
            snapshot = _project(verdict)
            GlobalRiskStore.instance().update(snapshot)
        except Exception as exc:
            logger.exception(
                "global_risk_subscriber.handle_failed",
                error=str(exc),
            )


# ── helpers ───────────────────────────────────────────────────────────────

def _extract_verdict(event: Any) -> Any:
    """Return the verdict object from the event, handling both dict and dataclass shapes."""
    # EventBus may wrap payload in an event dataclass with a .verdict attribute,
    # or the payload itself IS the verdict dict.
    if hasattr(event, "verdict"):
        return event.verdict
    if isinstance(event, dict) and "verdict" in event:
        return event["verdict"]
    # Fallback: treat event itself as the verdict
    return event


def _get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Attribute/key access with fallback — works on both dicts and dataclass/pydantic objects."""
    for key in keys:
        if isinstance(obj, dict):
            val = obj.get(key)
        else:
            val = getattr(obj, key, None)
        if val is not None:
            return val
    return default


def _project(verdict: Any) -> GlobalRiskSnapshot:
    """Map an EngineVerdict (or dict representation) to GlobalRiskSnapshot."""
    flagged_raw = _get(verdict, "flagged_tickers", "high_attention_tickers", default=[])
    flagged: list[str] = [str(t).upper() for t in flagged_raw] if flagged_raw else []

    action_raw = _get(verdict, "actions", "action_items", "recommended_actions", default=[])
    actions: list[str] = [str(a) for a in action_raw] if action_raw else []

    raw_dict: dict = verdict if isinstance(verdict, dict) else (verdict.__dict__ if hasattr(verdict, "__dict__") else {})

    return GlobalRiskSnapshot(
        flagged_tickers=flagged,
        risk_level=str(_get(verdict, "risk_level", "market_risk", default="unknown")),
        market_bias=str(_get(verdict, "market_bias", "bias", default="neutral")),
        confidence=float(_get(verdict, "confidence", default=0.0)),
        summary=str(_get(verdict, "summary", "reasoning_summary", "narrative", default="")),
        action_items=actions,
        raw_verdict=raw_dict,
    )
