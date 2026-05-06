"""
Proactive Alert Agent — AI Segment, Wave 3

Subscribes to SignalDetectedEvent from the event bus.
Calls AI to produce a structured ProactiveRecommendation.
Emits RecommendationReadyEvent back onto the bus.

Owner: ai segment.
Dependencies IN:  platform.event_bus, platform.events, ai.client, ai.schemas
Dependencies OUT: platform.events.RecommendationReadyEvent → bot / briefing segments

Boundary rules:
- No Discord/bot logic here — bot subscribes RecommendationReadyEvent.
- No watchlist mutation here — watchlist owns its own state.
- No thesis logic here — thesis segment owns thesis lifecycle.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from src.platform.event_bus import get_event_bus
from src.platform.events import (
    RecommendationReadyEvent,
    SignalDetectedEvent,
    ThesisReviewRequestedEvent,
)
from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.ai.client import AIClient

from src.ai.schemas import ProactiveRecommendation

logger = get_logger(__name__)

# ─── prompt ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
Bạn là AI phân tích cổ phiếu chuyên nghiệp cho thị trường chứng khoán Việt Nam (HOSE/HNX/UPCoM).
Nhiệm vụ: phân tích tín hiệu thị trường và đưa ra khuyến nghị hành động có cấu trúc.

Quy tắc:
1. Verdict phải là một trong: BUY / SELL / REDUCE / HOLD / WATCH
2. Urgency phải là một trong: NOW / TODAY / THIS_WEEK / MONITORING
3. Confidence từ 0.0 đến 1.0 — phản ánh mức độ chắc chắn thật sự, không thổi phồng
4. Risk signals: liệt kê tối đa 5 rủi ro cụ thể, ngắn gọn
5. Next watch items: tối đa 3 điều cần theo dõi tiếp theo
6. Luôn trả lời bằng JSON hợp lệ theo schema đã chỉ định
7. Reasoning phải ngắn gọn, có thể hành động ngay — không vòng vo lý thuyết
"""


def _build_user_prompt(event: SignalDetectedEvent) -> str:
    return f"""\
Tín hiệu phát hiện:
- Mã cổ phiếu: {event.symbol}
- Loại tín hiệu: {event.signal_type}
- Độ mạnh: {event.strength:.2f} / 1.0
- Confidence ban đầu: {event.confidence:.2f} / 1.0
- Nguồn: {event.source}
- Metadata: {event.metadata}

Hãy phân tích tín hiệu này và trả về khuyến nghị đầu tư theo JSON schema:
{{
  "symbol": "<mã CK>",
  "verdict": "BUY|SELL|REDUCE|HOLD|WATCH",
  "urgency": "NOW|TODAY|THIS_WEEK|MONITORING",
  "confidence": <0.0-1.0>,
  "reasoning": "<lý do ngắn gọn, 1-3 câu>",
  "risk_signals": ["<rủi ro 1>", "<rủi ro 2>"],
  "next_watch_items": ["<theo dõi 1>", "<theo dõi 2>"],
  "action": "<hành động cụ thể, ví dụ: Mua breakout trên 95,000>",
  "source_agent": "proactive_alert",
  "triggered_by_signal": "{event.signal_type}"
}}
"""


# ─── agent class ──────────────────────────────────────────────────────────────


class ProactiveAlertAgent:
    """
    Listens for SignalDetectedEvent, calls AI, emits RecommendationReadyEvent.

    Lifecycle:
        agent = ProactiveAlertAgent(ai_client)
        agent.register()   # subscribe to bus — call once at bootstrap
        ...                # bus worker handles events automatically
        agent.unregister() # (optional) if dynamic teardown needed
    """

    # Dedup window: same symbol + signal_type won't re-trigger AI within 60 min
    DEDUP_WINDOW = timedelta(minutes=60)

    # Urgency → signal_type mapping for thesis review side-effect
    _THESIS_REVIEW_SIGNAL_TYPES = {
        "THESIS_DIVERGENCE",
        "TREND_REVERSAL",
        "RISK_SPIKE",
    }

    def __init__(self, ai_client: "AIClient") -> None:
        self._client = ai_client
        self._registered = False

    def register(self) -> None:
        """Subscribe to SignalDetectedEvent on the global bus."""
        if self._registered:
            logger.warning("ProactiveAlertAgent already registered — skipping.")
            return
        bus = get_event_bus()
        bus.subscribe_handler(SignalDetectedEvent, self._handle)
        self._registered = True
        logger.info("ProactiveAlertAgent registered on event bus.")

    def unregister(self) -> None:
        """
        Remove handler from bus.
        Note: EventBus v1 does not support deregistration —
        this is a no-op placeholder for future implementation.
        """
        logger.warning(
            "ProactiveAlertAgent.unregister() called — "
            "EventBus v1 does not support handler removal. "
            "Restart process to deregister."
        )

    # ── internal handler ──────────────────────────────────────────────────────

    async def _handle(self, event: SignalDetectedEvent) -> None:  # type: ignore[override]
        """Core handler: AI call → emit recommendation."""
        logger.info(
            "proactive_alert.received",
            symbol=event.symbol,
            signal_type=event.signal_type,
            strength=event.strength,
            confidence=event.confidence,
        )

        try:
            recommendation = await self._call_ai(event)
        except Exception as exc:
            logger.exception(
                "proactive_alert.ai_error",
                symbol=event.symbol,
                signal_type=event.signal_type,
                error=str(exc),
            )
            return

        bus = get_event_bus()

        # Emit RecommendationReadyEvent → bot/briefing segments consume this
        rec_event = RecommendationReadyEvent(
            symbol=recommendation.symbol,
            action=recommendation.verdict,
            urgency=recommendation.urgency,
            confidence=recommendation.confidence,
            source_agent="proactive_alert",
        )
        await bus.publish(
            rec_event,
            dedup_key=f"{recommendation.symbol}:{recommendation.verdict}",
            dedup_window=self.DEDUP_WINDOW,
        )

        logger.info(
            "proactive_alert.recommendation_emitted",
            symbol=recommendation.symbol,
            verdict=recommendation.verdict,
            urgency=recommendation.urgency,
            confidence=recommendation.confidence,
            recommendation_id=rec_event.recommendation_id,
        )

        # Side-effect: if signal warrants thesis review, emit ThesisReviewRequestedEvent
        if event.signal_type in self._THESIS_REVIEW_SIGNAL_TYPES:
            thesis_id = event.metadata.get("thesis_id", "")
            if thesis_id:
                await bus.publish(
                    ThesisReviewRequestedEvent(
                        thesis_id=thesis_id,
                        symbol=event.symbol,
                        reason="signal",
                    ),
                    dedup_key=f"thesis_review:{thesis_id}",
                    dedup_window=timedelta(hours=4),
                )
                logger.info(
                    "proactive_alert.thesis_review_requested",
                    symbol=event.symbol,
                    thesis_id=thesis_id,
                )

    async def _call_ai(self, event: SignalDetectedEvent) -> ProactiveRecommendation:
        """Call AI client and parse structured response."""
        return await self._client.chat(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(event),
            response_schema=ProactiveRecommendation,
            temperature=0.2,
            max_tokens=1024,
        )


# ── module-level convenience ──────────────────────────────────────────────────

_agent_instance: ProactiveAlertAgent | None = None


def get_proactive_alert_agent(ai_client: "AIClient | None" = None) -> ProactiveAlertAgent:
    """
    Return the module-level ProactiveAlertAgent singleton.

    First call must pass ai_client. Subsequent calls may omit it.
    """
    global _agent_instance
    if _agent_instance is None:
        if ai_client is None:
            raise RuntimeError(
                "ProactiveAlertAgent not initialized. "
                "Call get_proactive_alert_agent(ai_client) once at bootstrap."
            )
        _agent_instance = ProactiveAlertAgent(ai_client)
    return _agent_instance


def reset_proactive_alert_agent() -> None:
    """Reset singleton — tests only."""
    global _agent_instance
    _agent_instance = None
