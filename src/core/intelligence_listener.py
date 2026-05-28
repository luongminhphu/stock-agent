"""
IntelligenceEngineListener — EventBus subscriber wiring the core engine.

Owner: core segment.

Consumed event : IntelligenceEngineRequestedEvent
Emitted event  : IntelligenceEngineCompletedEvent
Side-effect    : Pushes Discord embed to alert_channel when verdict is
                 actionable (confidence >= threshold and verdict != NO_ACTION/HOLD).

Wave 3 change:
    Passes event.signal_engine_summary into engine.run_cycle() so the
    AI verdict prompt receives richer cross-segment context.
    verdict_event_id echoed on IntelligenceEngineCompletedEvent so
    downstream feedback submissions can reference it.

Discord wiring:
    call .set_client(bot) in app.py after bootstrap (same pattern as
    BriefingListener and PostMortemSubscriber).
    channel_id defaults to settings.alert_channel_id when not provided.

Boot: call IntelligenceEngineListener(...).register() in platform bootstrap.
"""
from __future__ import annotations

import uuid
from typing import Any

from src.core import engine
from src.platform.event_bus import EventBus, get_event_bus
from src.platform.events import (
    IntelligenceEngineCompletedEvent,
    IntelligenceEngineRequestedEvent,
)
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Verdicts that always deserve a Discord push regardless of confidence
_ALWAYS_NOTIFY = {"RISK_ALERT", "SELL_SIGNAL"}
# Verdicts that are pushed only when confidence >= threshold
_NOTIFY_THRESHOLD = 0.65
# Verdicts that are silently skipped (no Discord push).
# HOLD is included: action_required=False makes a HOLD embed misleading.
_SILENT_VERDICTS = {"NO_ACTION", "HOLD"}


class IntelligenceEngineListener:
    """Subscribe to IntelligenceEngineRequestedEvent → run engine cycle → emit result.

    Optionally pushes a Discord embed to the configured alert channel when the
    verdict is actionable. Requires .set_client(bot) to be called before the
    first event fires (same lifecycle as BriefingListener).

    Args:
        bus:          EventBus instance. Defaults to get_event_bus() singleton.
        verdict_agent: Optional IntelligenceVerdictAgent (ai segment).
                       When provided, Wave 2 AI synthesis is active.
                       When None (default), Wave 1 heuristic runs only.
        channel_id:   Discord channel ID for verdict pushes.
                      Defaults to settings.alert_channel_id at first use.
    """

    def __init__(
        self,
        bus: EventBus | None = None,
        verdict_agent: Any | None = None,
        channel_id: int | None = None,
    ) -> None:
        self._bus = bus or get_event_bus()
        self._verdict_agent = verdict_agent
        self._channel_id = channel_id
        self._client: Any | None = None  # discord.Client injected by app.py

    def set_client(self, client: Any) -> None:
        """Inject Discord bot client. Called by app.py after bootstrap."""
        self._client = client
        logger.info("intelligence_listener.discord_client_injected")

    def _resolve_channel_id(self) -> int | None:
        """Resolve channel_id: constructor arg → settings.alert_channel_id."""
        if self._channel_id is not None:
            return self._channel_id
        try:
            from src.platform.config import settings
            raw = settings.alert_channel_id
            return int(raw) if raw else None
        except Exception:
            return None

    def register(self) -> None:
        self._bus.subscribe(IntelligenceEngineRequestedEvent, self._handle)
        logger.info(
            "intelligence_listener.registered",
            wave="2_ai" if self._verdict_agent is not None else "1_heuristic",
        )

    async def _handle(self, event: IntelligenceEngineRequestedEvent) -> None:
        logger.info(
            "intelligence_listener.received",
            trigger_source=event.trigger_source,
            priority=event.priority,
            user_id=event.user_id,
            has_signal_summary=bool(event.signal_engine_summary),
        )

        verdict = await engine.run_cycle(
            user_id=event.user_id,
            trigger_source=event.trigger_source,
            priority=event.priority,
            context_hint=event.context_hint,
            signal_engine_summary=event.signal_engine_summary,
            verdict_agent=self._verdict_agent,
        )

        if verdict is None:
            logger.info(
                "intelligence_listener.no_verdict",
                trigger_source=event.trigger_source,
                reason="below_threshold_or_snapshot_failed",
            )
            return

        # ── Emit IntelligenceEngineCompletedEvent ──────────────────────────
        # P0-A: echo verdict_event_id from EngineVerdict so EngineFeedbackListener
        # can cross-reference feedback submissions back to the originating verdict.
        echoed_verdict_event_id: str = (
            getattr(verdict, "verdict_event_id", None) or str(uuid.uuid4())
        )

        # P0-C (Option B): populate rich fields from EngineVerdict so
        # IntelligenceEngineCompletedEvent is self-contained for both
        # event bus consumers and Discord embed builder.
        def _to_tuple(val: Any) -> tuple[str, ...]:
            """Coerce list/tuple/None from EngineVerdict to tuple[str, ...]."""
            if not val:
                return ()
            return tuple(str(v) for v in val)

        completed = IntelligenceEngineCompletedEvent(
            verdict=verdict.verdict,
            confidence=verdict.confidence,
            action_required=verdict.verdict not in ("NO_ACTION", "HOLD"),
            summary=getattr(verdict, "action", "") or "",
            trigger_source=event.trigger_source,
            verdict_event_id=echoed_verdict_event_id,
            reasoning_summary=getattr(verdict, "reasoning_summary", "") or "",
            risk_signals=_to_tuple(getattr(verdict, "risk_signals", None)),
            next_watch_items=_to_tuple(getattr(verdict, "next_watch_items", None)),
            sources=_to_tuple(getattr(verdict, "sources", None)),
        )
        await self._bus.publish(completed)

        logger.info(
            "intelligence_listener.completed_emitted",
            verdict=completed.verdict,
            confidence=completed.confidence,
            action_required=completed.action_required,
            verdict_event_id=completed.verdict_event_id,
            risk_signal_count=len(completed.risk_signals),
            next_watch_count=len(completed.next_watch_items),
        )

        # ── Push Discord embed ─────────────────────────────────────────────
        # P0-C: pass completed event — fully self-contained, no raw verdict needed.
        await self._push_discord(completed)

    async def _push_discord(self, completed: IntelligenceEngineCompletedEvent) -> None:
        """Push engine verdict embed to Discord alert channel.

        Skipped when:
        - No Discord client injected (.set_client not called)
        - No channel_id configured
        - Verdict is in _SILENT_VERDICTS (NO_ACTION, HOLD)
        - Verdict is not RISK_ALERT/SELL_SIGNAL and confidence < threshold
        """
        verdict_type = completed.verdict.upper()
        confidence = completed.confidence

        if verdict_type in _SILENT_VERDICTS:
            logger.debug(
                "intelligence_listener.discord_skip",
                reason="silent_verdict",
                verdict=verdict_type,
            )
            return

        if verdict_type not in _ALWAYS_NOTIFY and confidence < _NOTIFY_THRESHOLD:
            logger.debug(
                "intelligence_listener.discord_skip",
                reason="below_confidence_threshold",
                verdict=verdict_type,
                confidence=confidence,
                threshold=_NOTIFY_THRESHOLD,
            )
            return

        if self._client is None:
            logger.warning(
                "intelligence_listener.discord_skip",
                reason="no_client_injected",
                verdict=verdict_type,
            )
            return

        channel_id = self._resolve_channel_id()
        if channel_id is None:
            logger.warning(
                "intelligence_listener.discord_skip",
                reason="no_channel_id_configured",
                verdict=verdict_type,
            )
            return

        channel = self._client.get_channel(channel_id)
        if channel is None:
            logger.warning(
                "intelligence_listener.discord_channel_not_found",
                channel_id=channel_id,
                verdict=verdict_type,
            )
            return

        try:
            from src.bot.discord_helper import build_engine_verdict_embed, safe_send
            embed = build_engine_verdict_embed(completed)
            await safe_send(channel, embed=embed)
            logger.info(
                "intelligence_listener.discord_sent",
                verdict=verdict_type,
                confidence=confidence,
                channel_id=channel_id,
                verdict_event_id=completed.verdict_event_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "intelligence_listener.discord_error",
                error=str(exc),
                verdict=verdict_type,
            )
