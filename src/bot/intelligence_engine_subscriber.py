"""IntelligenceEngineSubscriber — Discord delivery for engine verdicts.

Owner: bot segment.

Consumed event : IntelligenceEngineCompletedEvent
Side-effect    : Pushes Discord embed to alert_channel when verdict is
                 actionable (confidence >= threshold and verdict != NO_ACTION/HOLD).

Wiring (app.py on_ready):
    subscriber = IntelligenceEngineSubscriber(channel_id=...)
    subscriber.set_client(bot)
    subscriber.register()

This class mirrors the pattern of PostMortemSubscriber and
ProactiveWatchSubscriber — thin bot adapter, no domain logic.
"""
from __future__ import annotations

from typing import Any

from src.platform.event_bus import get_event_bus
from src.platform.events import IntelligenceEngineCompletedEvent
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Verdicts that always deserve a Discord push regardless of confidence
_ALWAYS_NOTIFY = {"RISK_ALERT", "SELL_SIGNAL"}
# Verdicts that are pushed only when confidence >= threshold
_NOTIFY_THRESHOLD = 0.65
# Verdicts silently skipped — HOLD embed is misleading when action_required=False
_SILENT_VERDICTS = {"NO_ACTION", "HOLD"}


class IntelligenceEngineSubscriber:
    """Subscribe to IntelligenceEngineCompletedEvent → push Discord embed.

    Requires .set_client(bot) before first event fires.

    Args:
        channel_id: Discord channel ID for verdict pushes.
                    Falls back to settings.alert_channel_id when None.
    """

    def __init__(self, channel_id: int | None = None) -> None:
        self._channel_id = channel_id
        self._client: Any | None = None

    def set_client(self, client: Any) -> None:
        """Inject Discord bot client. Called by app.py after bootstrap."""
        self._client = client
        logger.info("intelligence_engine_subscriber.discord_client_injected")

    def register(self) -> None:
        get_event_bus().subscribe(IntelligenceEngineCompletedEvent, self._handle)
        logger.info("intelligence_engine_subscriber.registered")

    def _resolve_channel_id(self) -> int | None:
        if self._channel_id is not None:
            return self._channel_id
        try:
            from src.platform.config import settings
            raw = settings.alert_channel_id
            return int(raw) if raw else None
        except Exception:
            return None

    async def _handle(self, event: IntelligenceEngineCompletedEvent) -> None:
        verdict_type = event.verdict.upper()
        confidence = event.confidence

        if verdict_type in _SILENT_VERDICTS:
            logger.debug(
                "intelligence_engine_subscriber.discord_skip",
                reason="silent_verdict",
                verdict=verdict_type,
            )
            return

        if verdict_type not in _ALWAYS_NOTIFY and confidence < _NOTIFY_THRESHOLD:
            logger.debug(
                "intelligence_engine_subscriber.discord_skip",
                reason="below_confidence_threshold",
                verdict=verdict_type,
                confidence=confidence,
                threshold=_NOTIFY_THRESHOLD,
            )
            return

        if self._client is None:
            logger.warning(
                "intelligence_engine_subscriber.discord_skip",
                reason="no_client_injected",
                verdict=verdict_type,
            )
            return

        channel_id = self._resolve_channel_id()
        if channel_id is None:
            logger.warning(
                "intelligence_engine_subscriber.discord_skip",
                reason="no_channel_id_configured",
                verdict=verdict_type,
            )
            return

        channel = self._client.get_channel(channel_id)
        if channel is None:
            logger.warning(
                "intelligence_engine_subscriber.discord_channel_not_found",
                channel_id=channel_id,
                verdict=verdict_type,
            )
            return

        try:
            from src.bot.discord_helper import build_engine_verdict_embed, safe_send
            embed = build_engine_verdict_embed(event)
            await safe_send(channel, embed=embed)
            logger.info(
                "intelligence_engine_subscriber.discord_sent",
                verdict=verdict_type,
                confidence=confidence,
                channel_id=channel_id,
                verdict_event_id=event.verdict_event_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "intelligence_engine_subscriber.discord_error",
                error=str(exc),
                verdict=verdict_type,
            )
